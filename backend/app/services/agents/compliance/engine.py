"""
ComplianceEngine: Main entry point for compliance analysis.
Orchestrates the LangGraph workflow and persists results.

Follows 12-Factor Agent principles:
- Acts as a Stateless Reducer: State_n+1 = f(State_n, Input)
"""
import logging
import traceback
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from datetime import datetime

try:
    from langsmith import traceable
except Exception:  # pragma: no cover
    def traceable(*_a, **_kw):  # type: ignore
        def _d(fn): return fn
        return _d if not (_a and callable(_a[0])) else _a[0]

from app.models.submission import Submission
from app.models.compliance_check import ComplianceCheck
from app.models.violation import Violation
from app.schemas.compliance_schemas import ComplianceAnalysisResult
from app.services.agents.compliance.scoring import scoring_service

logger = logging.getLogger(__name__)


class ComplianceEngine:
    """
    Core compliance analysis engine.
    Orchestrates the LangGraph workflow and persists results.
    """

    # Degraded reasons that block a passing grade but are not hard failures —
    # the document could not be substantively evaluated, so it needs review
    # rather than being marked "failed".
    _NEEDS_REVIEW_REASONS = {
        "knowledge_base_empty",
        "analysis_incomplete",
        "rag_degraded",
        "rules_unavailable",
        "disclosure_unavailable",
    }

    @staticmethod
    def evaluate_persistability(final_state: Dict[str, Any]) -> tuple:
        """Decide whether a graph run may be persisted as a real (gradeable)
        compliance result.

        Returns (can_persist, block_reason). A run is NOT persistable when it
        produced no analyzable content, the graph failed, the dispatch/analysis
        nodes flagged degradation, or any chunk failed to grade. This is the
        load-bearing fail-closed guard: an unevaluated document must never be
        recorded as 100/A/passed. See docs/architect-audit-2026-05-30.md (C1).
        """
        chunks = final_state.get("chunks") or []
        status = final_state.get("status")
        md = final_state.get("metadata") or {}

        if not chunks:
            return False, "no_content"
        if status == "failed":
            return False, "failed"
        degraded = md.get("degraded")
        if degraded:
            return False, degraded
        if md.get("analysis_failed_chunks"):
            return False, "analysis_incomplete"
        return True, None

    @staticmethod
    @traceable(run_type="chain", name="ComplianceEngine.analyze_submission")
    async def analyze_submission(
        submission_id: str,
        db: Session,
        *,
        triggered_by: Optional[str] = None,
        session_id: Optional[str] = None,
        trigger_source: str = "sync",
    ) -> Optional[ComplianceCheck]:
        """
        Entry point for compliance analysis using LangGraph.

        Args:
            submission_id: UUID of the submission to analyze
            db: Database session
            triggered_by: acting user id (for run/cost attribution); falls back to
                the submission's owner. session_id/trigger_source tag the run.

        Returns:
            ComplianceCheck if the run was gradeable and persisted; None if the
            run was not persistable (status set to 'needs_review' for degraded
            runs or 'failed' for hard failures — see evaluate_persistability).
        """
        submission = None
        try:
            # 1. Load submission under a row lock so two concurrent triggers
            #    can't both start an analysis (TOCTOU on status → duplicate
            #    checks + doubled LLM spend). The second waiter blocks here,
            #    then sees status="analyzing" and bails.
            submission = (
                db.query(Submission)
                .filter(Submission.id == submission_id)
                .with_for_update()
                .first()
            )
            if not submission:
                raise ValueError(f"Submission {submission_id} not found")

            if submission.status == "analyzing":
                logger.warning(
                    f"Submission {submission_id} is already being analyzed; "
                    f"skipping duplicate trigger (idempotency guard)."
                )
                db.commit()  # release the row lock
                return None

            # 2. Claim the submission. Commit releases the lock so readers see
            #    'analyzing' immediately.
            submission.status = "analyzing"
            db.commit()

            # 3. Initialize Graph Context
            from app.services.agents.orchestrator import orchestrator
            from app.services.agents.graph.context import GraphContext

            token = GraphContext.set_db_session(db)

            # 4. Build initial state
            initial_state = {
                "submission_id": str(submission_id),
                "user_id": str(submission.submitted_by) if submission.submitted_by else None,
                "chunks": [],
                "active_rules": {},
                "chunk_rules": {},
                "violations": [],
                "active_agents": [],
                "scores": {},
                "status": "running",
                "messages": [],
                "metadata": {},
                "user_feedback": None
            }

            # 5. Config for persistence (thread_id enables HITL checkpointing)
            config = {
                "configurable": {"thread_id": str(submission_id)},
                "metadata": {
                    "submission_id": str(submission_id),
                    "user_id": str(submission.submitted_by) if submission.submitted_by else None
                }
            }

            # 5b. Open a first-class analysis_runs row (covers re-runs AND
            #     fail-closed runs that persist no ComplianceCheck but still burn
            #     tokens) and bind the usage_context so every LLM call inside the
            #     graph attributes to this user/session/submission/run. Reset +
            #     rolled-up close happen in the finally below.
            import types as _types
            from app.services import run_tracker
            from app.services.observability.usage_context import (
                UsageContext, bind_usage_context, reset_usage_context,
            )

            actor_id = triggered_by or (str(submission.submitted_by) if submission.submitted_by else None)
            run = run_tracker.open(
                db, submission_id=submission_id,
                user=(_types.SimpleNamespace(id=actor_id) if actor_id else None),
                session_id=session_id, trigger_source=trigger_source,
            )
            usage_token = bind_usage_context(UsageContext(
                user_id=actor_id, session_id=session_id,
                submission_id=str(submission_id), run_id=str(run.id), feature="analysis",
            ))
            run_status, run_check_id, run_degraded = "failed", None, None

            try:
                logger.info(f"Starting LangGraph analysis for submission {submission_id}")

                # No HITL interrupt — the graph runs straight through to END, so
                # this returns the fully analyzed final state in one call.
                final_state = await orchestrator.run_workflow(initial_state, config=config)

                logger.info("LangGraph execution COMPLETED.")

                # FAIL CLOSED: never persist a passing grade for a run that
                # could not be substantively evaluated (no chunks, graph
                # failure, degraded retrieval, or any chunk that failed to
                # grade). A degraded document needs human review; a hard
                # failure is marked failed. See architect-audit C1.
                can_persist, block_reason = ComplianceEngine.evaluate_persistability(final_state)
                if not can_persist:
                    needs_review = block_reason in ComplianceEngine._NEEDS_REVIEW_REASONS
                    submission.status = "needs_review" if needs_review else "failed"
                    run_status, run_degraded = submission.status, block_reason
                    logger.error(
                        f"Refusing to persist gradeable result for submission "
                        f"{submission_id}: reason={block_reason}. "
                        f"Marking submission '{submission.status}' (NOT graded)."
                    )
                    db.commit()
                    return None

                # 6. Persist results. The submission status flip to 'analyzed'
                #    happens INSIDE persist_results, in the SAME transaction as
                #    the check + violations, so a crash can't leave a fully
                #    graded check attached to a submission still reading
                #    'analyzing' (torn audit record).
                compliance_check = ComplianceEngine.persist_results(
                    submission_id=str(submission_id),
                    violations=final_state.get("violations", []),
                    scores=final_state.get("scores", {}),
                    db=db
                )
                run_status, run_check_id = "completed", compliance_check.id

                # 7. Flip RAG chunk status to 'analyzed' so they become eligible
                # for cross-submission similarity search. Non-fatal on failure.
                try:
                    from app.services.rag.indexers.chunks_indexer import mark_submission_analyzed
                    summary = (
                        f"{submission.title} · score {compliance_check.overall_score}"
                        f" · grade {compliance_check.grade}"
                    )
                    await mark_submission_analyzed(
                        submission_id=str(submission_id), db=db, summary=summary
                    )
                except Exception as e:
                    logger.warning(f"RAG mark-analyzed failed (non-fatal): {e}")

                return compliance_check

            finally:
                # Reset the request-scoped DB session ContextVar so it does not
                # leak a (now closing) session into whatever task reuses this
                # context next. Previously this block was dead (`pass`), so the
                # token was never reset.
                GraphContext.reset(token)
                reset_usage_context(usage_token)
                # Close the run: roll up this run's tokens/cost, set status +
                # (nullable) compliance_check_id + duration. Runs for EVERY exit
                # path — success, fail-closed, and exceptions — so degraded runs
                # that spent tokens are still costed. Best-effort: a metering
                # failure must not mask the real result/exception.
                try:
                    run_tracker.close(
                        db, run, status=run_status,
                        degraded_reason=run_degraded, compliance_check_id=run_check_id,
                    )
                    from app.services.observability import audit as _audit
                    await _audit.record(
                        "analysis_finished", metadata={
                            "run_id": str(run.id), "run_number": run.run_number,
                            "status": run_status, "is_rerun": run.is_rerun,
                        },
                    )
                except Exception as _e:
                    logger.warning(f"run_tracker.close/audit failed (non-fatal): {_e}")

        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error analyzing submission {submission_id}: {str(e)}")
            db.rollback()
            if submission:
                try:
                    submission.status = "failed"
                    db.add(submission)
                    db.commit()
                except Exception:
                    pass
            raise

    @staticmethod
    def persist_results(
        submission_id: str,
        violations: List[Dict],
        scores: Dict,
        db: Session
    ) -> ComplianceCheck:
        """
        Persist compliance analysis results to the database.
        """
        try:
            # Create ComplianceCheck
            check = ComplianceCheck(
                submission_id=submission_id,
                overall_score=scores.get("overall", 0.0),
                grade=scores.get("grade", "F"),
                status=scores.get("status", "completed"),
                scores=scores,
                checked_at=datetime.utcnow()
            )
            db.add(check)
            db.flush()  # Get the ID

            # Persist violations
            ALLOWED_SEV = {"critical", "high", "medium", "low", "moderate", "informational"}
            _rule_version_cache: Dict[str, Optional[int]] = {}
            for v_data in violations:
                # Normalize severity + category casing at the boundary so the
                # LLM's "CRITICAL" / "Critical" / "critical" all stop forking
                # dashboard aggregations. Falls back to medium / unknown.
                raw_sev = str(v_data.get("severity", "medium")).strip().lower()
                sev = raw_sev if raw_sev in ALLOWED_SEV else "medium"
                cat = str(v_data.get("category", "unknown")).strip().lower() or "unknown"

                # Clamp confidence to [0,1]; default 0.85 when LLM doesn't supply.
                try:
                    conf_raw = v_data.get("confidence")
                    confidence = float(conf_raw) if conf_raw is not None else 0.85
                except (TypeError, ValueError):
                    confidence = 0.85
                confidence = max(0.0, min(1.0, confidence))

                # Coerce optional citation fields. cited_precedent_id is a UUID
                # in the rag_compliance_examples table; parse defensively so a
                # bad value from a non-precedent path doesn't break persistence.
                cited_precedent_id = None
                raw_pid = v_data.get("cited_precedent_id")
                if raw_pid:
                    try:
                        import uuid as _uuid
                        cited_precedent_id = _uuid.UUID(str(raw_pid))
                    except (ValueError, TypeError):
                        cited_precedent_id = None

                sim_score = v_data.get("similarity_score")
                try:
                    sim_score = float(sim_score) if sim_score is not None else None
                except (TypeError, ValueError):
                    sim_score = None

                violation = Violation(
                    compliance_check_id=check.id,
                    category=cat,
                    severity=sev,
                    description=v_data.get("description", ""),
                    location=v_data.get("location"),
                    current_text=v_data.get("current_text"),
                    suggested_fix=v_data.get("suggested_fix"),
                    auto_fixable=str(v_data.get("auto_fixable", False)).lower(),
                    chunk_index=v_data.get("chunk_index"),
                    confidence=confidence,
                    regulator_quote=v_data.get("regulator_quote"),
                    violation_metadata=v_data.get("violation_metadata"),
                    cited_precedent_id=cited_precedent_id,
                    cited_document_id=v_data.get("cited_document_id"),
                    cited_source_file=v_data.get("cited_source_file"),
                    cited_anchor_text=v_data.get("cited_anchor_text"),
                    cited_comment_verbatim=v_data.get("cited_comment_verbatim"),
                    cited_final_text=v_data.get("cited_final_text"),
                    similarity_score=sim_score,
                    # Citation locators (rule path) — exact clause/page/version.
                    cited_section=v_data.get("cited_section"),
                    cited_page=v_data.get("cited_page"),
                    cited_regulation_version=v_data.get("cited_regulation_version"),
                    # Sub-floor / uncertain findings persisted but kept out of the
                    # score and routed to human review.
                    suppressed=bool(v_data.get("suppressed", False)),
                    suppressed_reason=v_data.get("suppressed_reason"),
                )

                # Try to resolve rule_id as UUID + snapshot the rule's version so
                # a later rule edit/deactivation can't rewrite this decision.
                rule_id = v_data.get("rule_id")
                if rule_id:
                    try:
                        import uuid
                        rid = uuid.UUID(str(rule_id))
                        violation.rule_id = rid
                        key = str(rid)
                        if key not in _rule_version_cache:
                            from app.models.rule import Rule
                            _rule_version_cache[key] = (
                                db.query(Rule.version).filter(Rule.id == rid).scalar()
                            )
                        violation.rule_version = _rule_version_cache[key]
                    except (ValueError, TypeError):
                        pass

                db.add(violation)

            # Flip submission status in the SAME transaction as the check +
            # violations so the three commit atomically (no torn record).
            submission = (
                db.query(Submission).filter(Submission.id == submission_id).first()
            )
            if submission is not None:
                submission.status = "analyzed"
                db.add(submission)

            db.commit()
            db.refresh(check)

            logger.info(
                f"Persisted compliance check {check.id} with "
                f"{len(violations)} violations, score={scores.get('overall')} grade={scores.get('grade')}"
            )
            return check

        except Exception as e:
            logger.error(f"Failed to persist compliance results: {e}")
            db.rollback()
            raise

    @staticmethod
    async def get_check_summary(check_id: str, db: Session) -> Optional[Dict]:
        """Retrieve a summary of a compliance check."""
        check = db.query(ComplianceCheck).filter(ComplianceCheck.id == check_id).first()
        if not check:
            return None

        violations = db.query(Violation).filter(Violation.compliance_check_id == check.id).all()

        return {
            "id": str(check.id),
            "submission_id": str(check.submission_id),
            "overall_score": check.overall_score,
            "grade": check.grade,
            "status": check.status,
            "scores": check.scores,
            "checked_at": check.checked_at.isoformat() if check.checked_at else None,
            "violations": [
                {
                    "id": str(v.id),
                    "category": v.category,
                    "severity": v.severity,
                    "description": v.description,
                    "location": v.location,
                    "current_text": v.current_text,
                    "suggested_fix": v.suggested_fix,
                    "auto_fixable": v.auto_fixable,
                    "confidence": v.confidence,
                    "regulator_quote": v.regulator_quote,
                    "violation_metadata": v.violation_metadata,
                    "cited_precedent_id": str(v.cited_precedent_id) if v.cited_precedent_id else None,
                    "cited_document_id": v.cited_document_id,
                    "cited_source_file": v.cited_source_file,
                    "cited_anchor_text": v.cited_anchor_text,
                    "cited_comment_verbatim": v.cited_comment_verbatim,
                    "cited_final_text": v.cited_final_text,
                    "similarity_score": v.similarity_score,
                    "suppressed": bool(v.suppressed),
                    "suppressed_reason": v.suppressed_reason,
                }
                for v in violations
            ]
        }
