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

# Novel findings below this confidence are dropped before persistence — there
# is no historical reviewer to back them, so the bar is higher than for
# precedent-grounded citations (see 2026-05-28 reviewer-voice design).
NOVEL_CONFIDENCE_FLOOR = 0.75

# Severity is hard-coded for novel findings in v1 (open question in the design:
# should it be LLM-emitted?). Category is generic since novel findings carry
# their grounding in violation_metadata.regulatory_basis, not a precedent.
_NOVEL_SEVERITY = "moderate"
_NOVEL_CATEGORY = "regulatory"


def _citation_to_violation(
    c: Any, precedent: Dict[str, Any], *, chunk_id, chunk_index, location: str
) -> Dict[str, Any]:
    """Map one precedent citation to a violation dict. Severity/category/anchor/
    comment-verbatim/final-text are carried over from the retrieved precedent;
    the LLM supplies only the on-document reviewer_comment, action_type and
    evidence_needed (which land in violation_metadata)."""
    p = precedent
    return {
        "category": p.get("violation_category") or "other",
        "severity": p.get("severity") or "informational",
        "description": (c.reviewer_comment or "").strip(),
        "current_text": (c.current_text or "").strip(),
        "suggested_fix": p.get("final_text_chunk") or None,
        "auto_fixable": False,
        "confidence": float(c.confidence if c.confidence is not None else 0.85),
        "rule_id": None,
        "regulator_quote": None,
        # Citation columns — provenance back to the precedent.
        "cited_precedent_id": p.get("id"),
        "cited_document_id": p.get("document_id"),
        "cited_source_file": p.get("source_file"),
        "cited_anchor_text": p.get("anchor_text"),
        "cited_comment_verbatim": p.get("comment_text"),
        "cited_final_text": p.get("final_text_chunk"),
        "similarity_score": p.get("score"),
        "chunk_id": str(chunk_id),
        "chunk_index": chunk_index,
        "location": location,
        "violation_metadata": {
            "grounding": "precedent",
            "action_type": c.action_type,
            "evidence_needed": c.evidence_needed,
        },
    }


def _novel_finding_to_violation(
    f: Any, *, chunk_id, chunk_index, location: str
) -> Optional[Dict[str, Any]]:
    """Map one novel finding to a violation dict, or None if it falls below the
    confidence floor. Citation columns are NULL; grounding/regulatory_basis live
    in violation_metadata."""
    if float(f.confidence) < NOVEL_CONFIDENCE_FLOOR:
        return None
    return {
        "category": _NOVEL_CATEGORY,
        "severity": _NOVEL_SEVERITY,
        "description": (f.reviewer_comment or "").strip(),
        "current_text": (f.current_text or "").strip(),
        "suggested_fix": None,
        "auto_fixable": False,
        "confidence": float(f.confidence),
        "rule_id": None,
        "regulator_quote": None,
        "cited_precedent_id": None,
        "cited_document_id": None,
        "cited_source_file": None,
        "cited_anchor_text": None,
        "cited_comment_verbatim": None,
        "cited_final_text": None,
        "similarity_score": None,
        "chunk_id": str(chunk_id),
        "chunk_index": chunk_index,
        "location": location,
        "violation_metadata": {
            "grounding": "novel",
            "action_type": f.action_type,
            "evidence_needed": f.evidence_needed,
            "regulatory_basis": f.regulatory_basis,
        },
    }


def map_findings_to_violations(
    result: Any,
    precedents: List[Dict[str, Any]],
    *,
    chunk_id,
    chunk_index,
    location: str,
) -> List[Dict[str, Any]]:
    """Flatten a PrecedentCitationsResult into violation dicts. Out-of-range
    citation indices and sub-floor novel findings are dropped here."""
    out: List[Dict[str, Any]] = []
    for c in (result.citations or []):
        idx = int(c.precedent_index)
        if not (0 <= idx < len(precedents)):
            continue
        out.append(
            _citation_to_violation(
                c, precedents[idx], chunk_id=chunk_id, chunk_index=chunk_index, location=location
            )
        )
    for f in (result.novel_findings or []):
        v = _novel_finding_to_violation(
            f, chunk_id=chunk_id, chunk_index=chunk_index, location=location
        )
        if v is not None:
            out.append(v)
    return out


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
    # Guard: if preprocessing produced no chunks (empty document, extraction
    # failure, or DB returned nothing), mark it explicitly as "no_content" so
    # consumers can distinguish "not analyzed" from "clean document".  This is
    # set BEFORE the precedents block so it cannot be overwritten by the
    # "knowledge_base_empty" label below.
    if not chunks:
        md["degraded"] = "no_content"
        logger.warning(
            "dispatch_node: state contains zero chunks — preprocessing produced "
            "no analyzable content. This document will NOT be graded. "
            "Check preprocess_node logs for extraction/chunking errors."
        )

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
    # Only label "knowledge_base_empty" when chunks exist but the knowledge
    # base returned nothing — the empty-chunks case is already labelled
    # "no_content" above and must not be overwritten with a misleading reason.
    if chunks and total_precedents == 0:
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
    Compliance Specialist Node (precedent path): grades each chunk against its
    retrieved reviewer-decision precedents, in parallel. One LLM call per chunk
    at temperature 0. Output contract (ComplianceAnalysisResult) is unchanged.
    """
    logger.info("Node: Analysis (precedent) running...")

    from app.services.preprocessing_service import ContextEngineeringService
    from app.services.llm_service import llm_service
    from app.services.agents.validators import validate_agent_output
    from app.schemas.compliance_schemas import (
        ComplianceAnalysisResult,
        PrecedentCitationsResult,
    )
    from app.models.agent_execution import AgentExecution
    from app.database import SessionLocal

    chunks_data = state.get("chunks", [])
    retrieved = state.get("retrieved_examples") or {}
    submission_id = state.get("submission_id")
    user_id = state.get("user_id")

    new_violations: List[Dict] = []

    CORRECTIVE_SUFFIX = (
        "\n\nYour previous output had invalid fields. precedent_index must be an "
        "integer in [0, K-1] where K is the number of precedents, current_text "
        "must be a non-empty substring of the NEW DOCUMENT SECTION, description "
        "at least 5 characters, and confidence between 0 and 1."
    )

    # Bound concurrent per-chunk LLM grading to avoid connection-pool/rate-limit exhaustion.
    _grade_semaphore = asyncio.Semaphore(8)

    async def grade_chunk(chunk_data: Dict) -> List[Dict]:
        chunk_id = chunk_data.get("id")
        chunk_index = chunk_data.get("chunk_index")
        chunk_text = chunk_data.get("text", "")
        precedents = retrieved.get(str(chunk_id), [])
        # No early-return on empty precedents: the prompt switches to novel-only
        # mode so issues outside the corpus are still caught (2026-05-28 design).

        async with _grade_semaphore:
            task_db = None
            execution = None
            kept: List[Dict] = []
            try:
                task_db = SessionLocal()
                context_service = ContextEngineeringService(task_db)
                execution = AgentExecution(
                    agent_type="precedent",
                    session_id=uuid.UUID(submission_id) if submission_id else None,
                    user_id=uuid.UUID(user_id) if user_id else None,
                    status="running",
                    input_data={
                        "chunk_index": chunk_index,
                        "text_preview": chunk_text[:100],
                        "precedents_count": len(precedents),
                    },
                )
                task_db.add(execution)
                task_db.commit()

                prompt = context_service.create_precedent_prompts(chunk_text, precedents)
                system_prompt = (
                    "You are a senior Bajaj Allianz compliance reviewer. Cite "
                    "the historical precedents that apply to the new chunk. "
                    "Return ONLY valid JSON matching the required schema."
                )

                async def _call(p: str) -> PrecedentCitationsResult:
                    return await llm_service.generate_structured_response(
                        prompt=p,
                        output_model=PrecedentCitationsResult,
                        system_prompt=system_prompt,
                        execution_id=str(execution.id),
                        db=task_db,
                        tool_name="precedent_citation",
                        temperature=0.0,
                    )

                result = await _call(prompt)
                citations = list(result.citations or [])
                novel = list(result.novel_findings or [])

                # One corrective retry if any citation has an out-of-range
                # index. Keep whichever pass yields more in-range citations;
                # carry that pass's novel findings too.
                def _in_range(cs):
                    return [c for c in cs if 0 <= int(c.precedent_index) < len(precedents)]

                if citations and len(_in_range(citations)) < len(citations):
                    retry = await _call(prompt + CORRECTIVE_SUFFIX)
                    if len(_in_range(retry.citations or [])) >= len(_in_range(citations)):
                        citations = list(retry.citations or [])
                        novel = list(retry.novel_findings or [])
                citations = _in_range(citations)

                meta_loc = chunk_data.get("metadata", {})
                loc = f"chunk:{chunk_id}"
                if meta_loc.get("page_number"):
                    loc += f":page:{meta_loc['page_number']}"

                # Map citations (provenance carried over from the precedent) and
                # novel findings (regulatory_basis carried in metadata, sub-floor
                # findings dropped) into the shared violation shape.
                filtered = PrecedentCitationsResult(citations=citations, novel_findings=novel)
                for v in map_findings_to_violations(
                    filtered, precedents,
                    chunk_id=chunk_id, chunk_index=chunk_index, location=loc,
                ):
                    ok, errs = validate_agent_output(v)
                    if not ok:
                        _log_grade_error(chunk_id, v, errs)
                        continue
                    kept.append(v)

                execution.status = "completed"
                execution.output_data = {"violations": kept}
                task_db.commit()
            except Exception as e:
                logger.error(f"Precedent grading failed (chunk {chunk_index}): {e}")
                if task_db is not None and execution is not None:
                    try:
                        execution.status = "failed"
                        execution.output_data = {"error": str(e)}
                        task_db.commit()
                    except Exception:
                        pass
            finally:
                if task_db is not None:
                    task_db.close()
            return kept

    tasks = [grade_chunk(c) for c in chunks_data]
    if tasks:
        logger.info(f"Running {len(tasks)} per-chunk precedent grading tasks...")
        results = await asyncio.gather(*tasks)
        for res in results:
            new_violations.extend(res)

    return {
        "violations": new_violations,
        "messages": [AIMessage(content=f"Analysis: Found {len(new_violations)} violations (precedent path).")]
    }


def _log_grade_error(chunk_id, violation: Dict, errors: List[str]) -> None:
    import os
    import json as _json
    os.makedirs("logs", exist_ok=True)
    with open(os.path.join("logs", "grade_errors.log"), "a", encoding="utf-8") as f:
        f.write(_json.dumps({"chunk_id": str(chunk_id), "errors": errors, "violation": violation}) + "\n")


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
