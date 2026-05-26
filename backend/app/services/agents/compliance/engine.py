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

    @staticmethod
    @traceable(run_type="chain", name="ComplianceEngine.analyze_submission")
    async def analyze_submission(submission_id: str, db: Session) -> Optional[ComplianceCheck]:
        """
        Entry point for compliance analysis using LangGraph.
        
        Args:
            submission_id: UUID of the submission to analyze
            db: Database session
            
        Returns:
            ComplianceCheck object if completed, None if HITL pause
        """
        submission = None
        try:
            # 1. Load submission
            submission = db.query(Submission).filter(Submission.id == submission_id).first()
            if not submission:
                raise ValueError(f"Submission {submission_id} not found")

            # 2. Update status
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

            try:
                logger.info(f"Starting LangGraph analysis for submission {submission_id}")

                final_state = await orchestrator.run_workflow(initial_state, config=config)

                # If the graph paused at the HITL refinement_node, auto-resume
                # with no feedback so analysis finishes in a single call. The
                # refinement_node is a no-op without user_feedback; the explicit
                # /compliance/resume endpoint remains for HITL flows that DO
                # have feedback to apply.
                snapshot = await orchestrator.get_state(config)
                if snapshot.next:
                    logger.info(
                        f"LangGraph paused at {snapshot.next} for submission "
                        f"{submission_id}; auto-resuming (no feedback)."
                    )
                    final_state = await orchestrator.resume_workflow(config)
                    snapshot = await orchestrator.get_state(config)
                    if snapshot.next:
                        # Still paused — true HITL hold; surface to caller.
                        submission.status = "waiting_for_review"
                        db.commit()
                        return None

                logger.info("LangGraph execution COMPLETED.")

                # Guard: if preprocessing failed (no chunks) or the graph
                # marked status=failed, do NOT persist a fake 100/A. That
                # silent-success on a broken pipeline was masking real
                # errors (e.g. tiktoken DNS block on corporate VPN).
                chunk_count = len(final_state.get("chunks") or [])
                graph_status = final_state.get("status")
                if chunk_count == 0 or graph_status == "failed":
                    logger.error(
                        f"Refusing to persist results: chunks={chunk_count}, "
                        f"graph_status={graph_status}. Marking submission failed."
                    )
                    submission.status = "failed"
                    db.commit()
                    return None

                # 6. Persist results
                compliance_check = ComplianceEngine.persist_results(
                    submission_id=str(submission_id),
                    violations=final_state.get("violations", []),
                    scores=final_state.get("scores", {}),
                    db=db
                )

                submission.status = "analyzed"
                db.commit()

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
                # Reset context variable
                from contextvars import copy_context
                pass

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
                )

                # Try to resolve rule_id as UUID
                rule_id = v_data.get("rule_id")
                if rule_id:
                    try:
                        import uuid
                        violation.rule_id = uuid.UUID(str(rule_id))
                    except (ValueError, TypeError):
                        pass

                db.add(violation)

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
                    "auto_fixable": v.auto_fixable
                }
                for v in violations
            ]
        }
