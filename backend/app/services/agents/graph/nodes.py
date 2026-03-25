"""
LangGraph Nodes for Regulatory Compliance Workflow.

Graph flow:
    start → preprocess_node → dispatch_node → analysis_node → scoring_node → refinement_node → end

Each node is a pure async function that takes ComplianceState and returns partial state updates.
"""
import logging
import uuid
import datetime
import asyncio
from typing import Dict, Any, List
from langchain_core.messages import AIMessage

from .state import ComplianceState

logger = logging.getLogger(__name__)


async def preprocess_node(state: ComplianceState) -> Dict:
    """
    Librarian Node: Prepares document content into chunks.
    """
    logger.info("Node: Preprocess (Librarian) running...")

    from app.services.preprocessing_service import ContextEngineeringService
    from app.models.content_chunk import ContentChunk
    from .context import GraphContext

    db = GraphContext.get_db_session()
    submission_id = state["submission_id"]

    service = ContextEngineeringService(db)
    try:
        chunk_count = await service.preprocess_submission(uuid.UUID(submission_id))
        logger.info(f"Preprocessing complete. Chunk count: {chunk_count}")

        # Load chunks into state
        submission_chunks = db.query(ContentChunk).filter(
            ContentChunk.submission_id == submission_id
        ).order_by(ContentChunk.chunk_index).all()

        chunks_data = [
            {
                "id": str(c.id),
                "text": c.text,
                "chunk_index": c.chunk_index,
                "metadata": c.chunk_metadata or {}
            }
            for c in submission_chunks
        ]

        return {
            "chunks": chunks_data,
            "messages": [AIMessage(content=f"Librarian: Prepared {len(chunks_data)} chunks.")]
        }

    except Exception as e:
        logger.error(f"Preprocessing failed: {e}")
        return {
            "messages": [AIMessage(content=f"Librarian: Error during preprocessing - {str(e)}")],
            "status": "failed"
        }


async def dispatch_node(state: ComplianceState) -> Dict:
    """
    Brain Node: Identifies active rules and determines execution plan.
    """
    logger.info("Node: Dispatch (Brain) running...")

    from app.services.rule_generator_service import rule_generator_service
    from .context import GraphContext

    db = GraphContext.get_db_session()

    # Load active rules
    rules_orm = rule_generator_service.get_active_rules(db)

    rules_serializable = {}
    active_agents = []

    for cat, r_list in rules_orm.items():
        if r_list:
            rules_serializable[cat] = [
                {
                    "id": str(r.id),
                    "rule_text": r.rule_text,
                    "category": r.category,
                    "severity": r.severity
                }
                for r in r_list
            ]
            active_agents.append(f"agent_{cat}")

    active_rule_count = sum(len(v) for v in rules_serializable.values())

    return {
        "active_rules": rules_serializable,
        "active_agents": active_agents,
        "messages": [AIMessage(content=f"Brain: Dispatched {len(active_agents)} agents. Active Rules: {active_rule_count}")]
    }


async def analysis_node(state: ComplianceState) -> Dict:
    """
    Compliance Specialist Node: Runs sub-agents on chunks against rules in parallel.
    """
    logger.info("Node: Analysis running...")

    from app.services.preprocessing_service import ContextEngineeringService
    from app.services.agents.agent_factory import AgentFactory
    from app.models.agent_execution import AgentExecution
    from app.models.tool_invocation import ToolInvocation
    from app.database import SessionLocal
    from .context import GraphContext

    chunks_data = state.get("chunks", [])
    rules = state.get("active_rules", {})
    submission_id = state.get("submission_id")
    user_id = state.get("user_id")

    active_categories = [cat for cat, r_list in rules.items() if r_list]
    new_violations = []

    async def process_task(category: str, chunk_data: Dict) -> List[Dict]:
        chunk_text = chunk_data.get("text", "")
        chunk_index = chunk_data.get("chunk_index")
        chunk_id = chunk_data.get("id")

        task_violations = []
        task_db = SessionLocal()

        try:
            context_service = ContextEngineeringService(task_db)
            agent = AgentFactory.create_agent(category, context_service)

            # Record execution
            execution = AgentExecution(
                agent_type=category,
                session_id=uuid.UUID(submission_id) if submission_id else None,
                user_id=uuid.UUID(user_id) if user_id else None,
                status="running",
                input_data={"chunk_index": chunk_index, "text_preview": chunk_text[:100]}
            )
            task_db.add(execution)
            task_db.commit()

            try:
                start_time = datetime.datetime.now()
                analysis_result = await agent.analyze(
                    content=chunk_text,
                    rules=rules[category],
                    execution_id=str(execution.id),
                    db=task_db
                )
                end_time = datetime.datetime.now()

                execution.status = "completed"
                execution.output_data = analysis_result.model_dump(mode='json')
                execution.completed_at = end_time
                execution.execution_time_ms = str(int((end_time - start_time).total_seconds() * 1000))

                tool_invocations = task_db.query(ToolInvocation).filter(
                    ToolInvocation.execution_id == execution.id
                ).all()
                execution.total_tokens_used = str(sum(inv.tokens_used for inv in tool_invocations))
                task_db.commit()

                for v in analysis_result.violations:
                    v_dict = v.model_dump()
                    v_dict["chunk_id"] = str(chunk_id)
                    v_dict["chunk_index"] = chunk_index

                    loc = f"chunk:{chunk_id}"
                    meta = chunk_data.get("metadata", {})
                    if meta.get("page_number"):
                        loc += f":page:{meta['page_number']}"
                    v_dict["location"] = loc

                    task_violations.append(v_dict)

            except Exception as e:
                logger.error(f"Task failed ({category}, chunk {chunk_index}): {e}")
                execution.status = "failed"
                execution.output_data = {"error": str(e)}
                task_db.commit()

        except Exception as e:
            logger.error(f"DB Error in task: {e}")
        finally:
            task_db.close()

        return task_violations

    # Create and run all tasks in parallel
    tasks = [
        process_task(cat, chunk)
        for chunk in chunks_data
        for cat in active_categories
    ]

    if tasks:
        logger.info(f"Running {len(tasks)} analysis tasks in parallel...")
        results = await asyncio.gather(*tasks)
        for res in results:
            new_violations.extend(res)

    return {
        "violations": new_violations,
        "messages": [AIMessage(content=f"Analysis: Found {len(new_violations)} violations.")]
    }


async def scoring_node(state: ComplianceState) -> Dict:
    """
    Scoring Node: Calculates final compliance grades.
    """
    logger.info("Node: Scoring running...")

    from app.services.agents.compliance.scoring import scoring_service
    from .context import GraphContext

    db = GraphContext.get_db_session()
    violations = state.get("violations", [])
    active_rules = state.get("active_rules", {})
    categories = list(active_rules.keys())

    scores = scoring_service.calculate_scores(violations, db=db, categories=categories)

    return {
        "scores": scores,
        "messages": [AIMessage(content=f"Scoring: Grade {scores.get('grade')} ({scores.get('overall')}%)")]
    }


async def refinement_node(state: ComplianceState) -> Dict:
    """
    Refinement Node: HITL Review point.
    Graph is set to interrupt_before this node for human review.
    """
    logger.info("Node: Refinement (HITL) running...")

    feedback = state.get("user_feedback")
    if feedback:
        return {
            "messages": [AIMessage(content=f"Refinement: Processed human feedback: {feedback}")]
        }

    return {"messages": [AIMessage(content="Refinement: No feedback, proceeding to completion.")]}
