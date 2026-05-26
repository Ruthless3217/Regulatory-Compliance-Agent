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
from typing import Dict, Any, List, Optional
from langchain_core.messages import AIMessage

try:
    from langsmith import traceable
except Exception:  # pragma: no cover
    def traceable(*_a, **_kw):  # type: ignore
        def _d(fn): return fn
        return _d if not (_a and callable(_a[0])) else _a[0]

from .state import ComplianceState

logger = logging.getLogger(__name__)


@traceable(run_type="chain", name="graph.preprocess_node")
async def preprocess_node(state: ComplianceState) -> Dict:
    """
    Librarian Node: Prepares document content into chunks, then mirrors
    them into the RAG `rag_chunks` index. Indexing failure is non-fatal —
    it degrades downstream retrieval but does not block analysis.
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

        # Mirror chunks into RAG index (non-fatal on failure).
        rag_indexed = 0
        try:
            from app.services.rag.indexers.chunks_indexer import upsert_chunks_for_submission
            rag_indexed = await upsert_chunks_for_submission(
                submission_id=submission_id, db=db, submission_status="analyzing"
            )
        except Exception as e:
            logger.warning(f"RAG chunk indexing failed (non-fatal): {e}")

        return {
            "chunks": chunks_data,
            "messages": [AIMessage(
                content=f"Librarian: Prepared {len(chunks_data)} chunks "
                        f"(RAG indexed: {rag_indexed})."
            )]
        }

    except Exception as e:
        logger.error(f"Preprocessing failed: {e}")
        return {
            "messages": [AIMessage(content=f"Librarian: Error during preprocessing - {str(e)}")],
            "status": "failed"
        }


@traceable(run_type="chain", name="graph.dispatch_node")
async def dispatch_node(state: ComplianceState) -> Dict:
    """
    Brain Node: Identifies active rules and determines execution plan.

    RAG-aware: tries to fetch top-K most-relevant rules per chunk via the
    rules retriever (semantic + keyword hybrid). Falls back to the legacy
    "all active rules per category" path on any RAG failure.
    """
    logger.info("Node: Dispatch (Brain) running...")

    from app.config import settings
    from app.services.rule_generator_service import rule_generator_service
    from .context import GraphContext

    db = GraphContext.get_db_session()

    # 1. Always load full active rules (fallback target + populates active_agents).
    rules_orm = rule_generator_service.get_active_rules(db)
    rules_serializable: Dict[str, List[Dict]] = {}
    active_agents: List[str] = []
    for cat, r_list in rules_orm.items():
        if r_list:
            rules_serializable[cat] = [
                {
                    "id": str(r.id),
                    "rule_text": r.rule_text,
                    "category": r.category,
                    "severity": r.severity,
                    "keywords": r.keywords or [],
                }
                for r in r_list
            ]
            active_agents.append(f"agent_{cat}")

    active_rule_count = sum(len(v) for v in rules_serializable.values())

    # 2. Try RAG per-chunk retrieval. On any failure, set rag_degraded=true
    #    and let analysis_node use the flat rules_serializable.
    chunks = state.get("chunks", [])
    categories = list(rules_serializable.keys())
    chunk_rules: Dict[str, Dict[str, List[Dict]]] = {}
    rag_degraded = False

    if chunks and categories:
        try:
            from app.services.rag.retrievers.rules_retriever import get_rules_retriever
            retriever = get_rules_retriever()
            chunk_rules = await retriever.retrieve_per_chunk(
                chunks=chunks,
                categories=categories,
                top_k=settings.rag_top_k_analysis,
            )
            retrieved_total = sum(
                len(rs) for chunk_map in chunk_rules.values() for rs in chunk_map.values()
            )
            logger.info(
                f"RAG: retrieved {retrieved_total} rule slots across "
                f"{len(chunk_rules)} chunks × {len(categories)} categories"
            )
            # P1.3 — Enrich each retrieved rule with its regulator source
            # passage (best-effort lookup from rag_source_docs by rule_id).
            # The analysis prompt copies this verbatim into the violation's
            # `regulator_quote` field. Non-fatal on failure.
            try:
                from app.services.rag.retrievers.source_docs_retriever import (
                    get_source_docs_retriever,
                )
                src_retr = get_source_docs_retriever()
                for chunk_map in chunk_rules.values():
                    for rule_list in chunk_map.values():
                        for r in rule_list:
                            rule_id = r.get("id")
                            if not rule_id or r.get("source_quote"):
                                continue
                            try:
                                passages = src_retr.by_rule(rule_id, limit=1)
                                if passages:
                                    r["source_quote"] = (passages[0].get("text") or "")[:300]
                            except Exception:
                                pass
            except Exception as e:
                logger.debug(f"Citation enrichment skipped (non-fatal): {e}")
        except Exception as e:
            logger.warning(f"RAG rule retrieval failed; falling back to all-rules: {e}")
            rag_degraded = True

    md = dict(state.get("metadata") or {})
    md["rag_degraded"] = rag_degraded
    md["rag_rules_per_chunk"] = (
        {cid: sum(len(rs) for rs in cm.values()) for cid, cm in chunk_rules.items()}
        if chunk_rules else {}
    )

    # --- Precedent path (primary analysis driver) ---
    retrieved_examples: Dict[str, List[Dict]] = {}
    try:
        from app.services.rag.retrievers.precedent_retriever import get_precedent_retriever
        precedent_retriever = get_precedent_retriever()
        retrieved_examples = await precedent_retriever.retrieve_per_chunk(
            chunks=chunks, top_k=settings.pgvector_top_k
        )
    except Exception as e:
        logger.warning(f"Precedent retrieval failed (analysis will find no violations): {e}")
        retrieved_examples = {str(c.get("id")): [] for c in chunks}

    total_precedents = sum(len(v) for v in retrieved_examples.values())
    if total_precedents == 0:
        md["degraded"] = "knowledge_base_empty"
        logger.warning(
            "Knowledge base returned ZERO precedents across all chunks — "
            "analysis will produce no violations. Ingest the precedent corpus "
            "(scripts.ingest_knowledge_base) to enable grading."
        )
    md["precedents_per_chunk"] = {cid: len(v) for cid, v in retrieved_examples.items()}

    if "agent_precedent" not in active_agents:
        active_agents.append("agent_precedent")

    return {
        "active_rules": rules_serializable,
        "chunk_rules": chunk_rules,
        "retrieved_examples": retrieved_examples,
        "active_agents": active_agents,
        "metadata": md,
        "messages": [AIMessage(
            content=(
                f"Brain: Dispatched precedent analysis over {len(chunks)} chunks "
                f"({total_precedents} precedents retrieved). "
                f"Legacy rules loaded: {active_rule_count}."
            )
        )]
    }


@traceable(run_type="chain", name="graph.analysis_node")
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
    chunk_rules_map = state.get("chunk_rules") or {}
    submission_id = state.get("submission_id")
    user_id = state.get("user_id")

    active_categories = [cat for cat, r_list in rules.items() if r_list]
    new_violations = []

    def _rules_for(category: str, chunk_id: Optional[str]) -> List[Dict]:
        """Prefer RAG per-chunk rules; fall back to the flat per-category list."""
        if chunk_id and chunk_id in chunk_rules_map:
            per_cat = chunk_rules_map[chunk_id].get(category)
            if per_cat:
                return per_cat
        return rules.get(category, [])

    async def process_task(category: str, chunk_data: Dict) -> List[Dict]:
        chunk_text = chunk_data.get("text", "")
        chunk_index = chunk_data.get("chunk_index")
        chunk_id = chunk_data.get("id")
        task_rules = _rules_for(category, chunk_id)

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
                input_data={
                    "chunk_index": chunk_index,
                    "text_preview": chunk_text[:100],
                    "rules_count": len(task_rules),
                }
            )
            task_db.add(execution)
            task_db.commit()

            try:
                start_time = datetime.datetime.now()
                analysis_result = await agent.analyze(
                    content=chunk_text,
                    rules=task_rules,
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

                primary_violations = []
                for v in analysis_result.violations:
                    v_dict = v.model_dump()
                    v_dict["chunk_id"] = str(chunk_id)
                    v_dict["chunk_index"] = chunk_index

                    loc = f"chunk:{chunk_id}"
                    meta = chunk_data.get("metadata", {})
                    if meta.get("page_number"):
                        loc += f":page:{meta['page_number']}"
                    v_dict["location"] = loc

                    primary_violations.append(v_dict)

                # P1.4 — Critic LLM pass. Independently reviews each
                # violation against the cited rule + the chunk text. Drops
                # hallucinated rule_ids and downgrades borderline findings.
                from app.services.agents.compliance.critic import critique_violations
                task_violations.extend(
                    await critique_violations(chunk_text, task_rules, primary_violations)
                )

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


@traceable(run_type="chain", name="graph.scoring_node")
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
