"""
Compliance Analysis Routes

Endpoints:
- POST /compliance/analyze/{submission_id}        - Trigger compliance analysis (background)
- POST /compliance/analyze/{submission_id}/sync   - Synchronous analysis
- POST /compliance/analyze/{submission_id}/stream - SSE-streamed analysis with progress
- GET  /compliance/results/{submission_id}        - Get analysis results
- GET  /compliance/check/{check_id}               - Get specific check details
- POST /compliance/resume/{submission_id}         - Resume HITL workflow
- POST /compliance/violations/{violation_id}/feedback - Reviewer verdict (adaptive weights)
- POST /compliance/check/{check_id}/reviewer-score    - Held-out reviewer score (eval only)
"""
import asyncio
import json
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Literal, Optional, Set

from app.api.rate_limit import llm_rate_limit
from app.database import get_db, SessionLocal
from app.models.submission import Submission
from app.models.compliance_check import ComplianceCheck
from app.models.violation import Violation
from app.services.agents.compliance.engine import ComplianceEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/compliance", tags=["Compliance Analysis"])


@router.post("/analyze/{submission_id}", dependencies=[Depends(llm_rate_limit)])
async def analyze_submission(
    submission_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Trigger compliance analysis for a submission.
    Runs asynchronously in the background.
    """
    # Check submission exists
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    if submission.status in ("analyzing",):
        return {"message": "Analysis already in progress", "submission_id": submission_id}

    # Queue analysis as a background task
    background_tasks.add_task(_run_analysis, submission_id)

    return {
        "message": "Compliance analysis started",
        "submission_id": submission_id,
        "status": "analyzing"
    }


async def _run_analysis(submission_id: str):
    """Background task runner for compliance analysis."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        await ComplianceEngine.analyze_submission(submission_id, db)
        logger.info(f"Background analysis completed for {submission_id}")
    except Exception as e:
        logger.error(f"Background analysis failed for {submission_id}: {e}")
    finally:
        db.close()


@router.post("/analyze/{submission_id}/sync", dependencies=[Depends(llm_rate_limit)])
async def analyze_submission_sync(
    submission_id: str,
    db: Session = Depends(get_db)
):
    """
    Trigger compliance analysis and wait for results (synchronous).
    Use for testing or small submissions.
    """
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    try:
        compliance_check = await ComplianceEngine.analyze_submission(submission_id, db)

        if compliance_check is None:
            # Not persistable: the run was degraded (-> 'needs_review') or hit a
            # hard failure (-> 'failed'). Surface the real status the engine set.
            db.refresh(submission)
            return {
                "status": submission.status,
                "submission_id": submission_id,
                "message": (
                    "Analysis could not be graded (document could not be "
                    "substantively evaluated). See server logs for the reason."
                ),
            }

        return {
            "status": "completed",
            "submission_id": submission_id,
            "check_id": str(compliance_check.id),
            "overall_score": compliance_check.overall_score,
            "grade": compliance_check.grade,
            "compliance_status": compliance_check.status
        }
    except Exception as e:
        # Log the detail server-side; don't leak internals to the client (audit).
        logger.error(f"Sync analysis failed for {submission_id}: {e}")
        raise HTTPException(status_code=500, detail="Analysis failed; see server logs.")


def _sse(event: str, data) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _status_to_stage(status: str) -> str:
    """Map submission status to a public stage label."""
    mapping = {
        "uploaded": "preprocess",
        "preprocessing": "preprocess",
        "preprocessed": "dispatch",
        "analyzing": "analysis",
        "analyzed": "scoring",
        "failed": "error",
    }
    return mapping.get(status, "analysis")


def _stage_progress(status: str) -> float:
    mapping = {
        "uploaded": 0.1,
        "preprocessing": 0.2,
        "preprocessed": 0.4,
        "analyzing": 0.6,
        "analyzed": 1.0,
        "failed": 1.0,
    }
    return mapping.get(status, 0.5)


def _serialize_violation(v: Violation) -> dict:
    return {
        "id": str(v.id),
        "category": v.category,
        "severity": v.severity,
        "description": v.description,
        "location": v.location,
        "current_text": v.current_text,
        "suggested_fix": v.suggested_fix,
        "auto_fixable": v.auto_fixable,
        "chunk_index": v.chunk_index,
        "rule_id": str(v.rule_id) if v.rule_id else None,
        "confidence": v.confidence,
        "regulator_quote": v.regulator_quote,
        # Reviewer-voice tags (2026-05-28): action_type, evidence_needed,
        # grounding (precedent|novel), regulatory_basis. Live in JSONB; the UI
        # renders them as the action/needed/source badge row.
        "violation_metadata": v.violation_metadata,
        # Precedent-citation provenance (Phase 1.5). All fields are nullable;
        # populated only when the violation came from the precedent path.
        "cited_precedent_id": str(v.cited_precedent_id) if v.cited_precedent_id else None,
        "cited_document_id": v.cited_document_id,
        "cited_source_file": v.cited_source_file,
        "cited_anchor_text": v.cited_anchor_text,
        "cited_comment_verbatim": v.cited_comment_verbatim,
        "cited_final_text": v.cited_final_text,
        "similarity_score": v.similarity_score,
        # Sub-confidence-floor / structural findings: persisted but kept out of
        # the score and surfaced in a separate "Needs review" lane in the UI.
        "suppressed": bool(v.suppressed),
        "suppressed_reason": v.suppressed_reason,
    }


async def _analyze_and_stream(submission_id: str):
    """
    Generator: spawns analyze in a task and polls DB state, emitting SSE events
    for stage transitions, new violations, and final score.
    Existing analyze pipeline is untouched.
    """
    # Validate submission exists in its own DB session
    init_db = SessionLocal()
    try:
        submission = init_db.query(Submission).filter(Submission.id == submission_id).first()
        if not submission:
            yield _sse("error", {"message": "Submission not found"})
            return
    finally:
        init_db.close()

    # Kick off analysis (background coroutine)
    async def _runner():
        db = SessionLocal()
        try:
            await ComplianceEngine.analyze_submission(submission_id, db)
        except Exception as e:
            logger.error(f"SSE analyze runner failed for {submission_id}: {e}")
        finally:
            db.close()

    analyze_task = asyncio.create_task(_runner())

    # Poll loop — emit on state changes
    last_status: Optional[str] = None
    seen_violation_ids: Set[str] = set()
    last_score_emitted = False

    try:
        while True:
            db = SessionLocal()
            try:
                sub = db.query(Submission).filter(Submission.id == submission_id).first()
                if not sub:
                    yield _sse("error", {"message": "Submission disappeared"})
                    return

                # Stage event on transition
                if sub.status != last_status:
                    last_status = sub.status
                    yield _sse(
                        "stage",
                        {"stage": _status_to_stage(sub.status), "progress": _stage_progress(sub.status)},
                    )

                # Latest check + any new violations
                check = (
                    db.query(ComplianceCheck)
                    .filter(ComplianceCheck.submission_id == submission_id)
                    .order_by(ComplianceCheck.checked_at.desc())
                    .first()
                )
                if check:
                    new_violations = (
                        db.query(Violation)
                        .filter(Violation.compliance_check_id == check.id)
                        .all()
                    )
                    fresh = [v for v in new_violations if str(v.id) not in seen_violation_ids]
                    if fresh:
                        for v in fresh:
                            seen_violation_ids.add(str(v.id))
                        # Group by chunk_index for the "chunk" event shape
                        by_chunk: dict[int, list] = {}
                        for v in fresh:
                            by_chunk.setdefault(v.chunk_index or 0, []).append(_serialize_violation(v))
                        for chunk_index, violations in by_chunk.items():
                            yield _sse(
                                "chunk",
                                {
                                    "chunk_index": chunk_index,
                                    "category": violations[0]["category"],
                                    "violations": violations,
                                },
                            )

                    # Score event when complete
                    if sub.status == "analyzed" and check.overall_score is not None and not last_score_emitted:
                        yield _sse(
                            "score",
                            {
                                "overall_score": check.overall_score,
                                "grade": check.grade,
                                "scores": check.scores or {},
                            },
                        )
                        last_score_emitted = True

                # Terminal conditions
                if sub.status == "analyzed" and last_score_emitted:
                    yield _sse("done", {"check_id": str(check.id) if check else None})
                    return
                if sub.status == "failed":
                    yield _sse("error", {"message": "Analysis failed"})
                    return
                if sub.status == "needs_review":
                    # Degraded run (e.g. retrieval/grading incomplete): nothing
                    # gradeable was persisted. Terminate instead of polling forever.
                    yield _sse("error", {
                        "message": "Analysis could not be completed — document needs review."
                    })
                    return
                if analyze_task.done() and analyze_task.exception() is not None:
                    yield _sse("error", {"message": f"Analyzer error: {analyze_task.exception()}"})
                    return
            finally:
                db.close()

            await asyncio.sleep(0.7)
    finally:
        if not analyze_task.done():
            # Let it finish in background — don't cancel mid-flight
            pass


@router.post("/analyze/{submission_id}/stream", dependencies=[Depends(llm_rate_limit)])
async def analyze_submission_stream(submission_id: str):
    """SSE-stream analysis progress: stage / chunk / score / done / error."""
    return StreamingResponse(
        _analyze_and_stream(submission_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/results/{submission_id}")
async def get_compliance_results(
    submission_id: str,
    db: Session = Depends(get_db)
):
    """Get the latest compliance analysis results for a submission."""
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    # Get latest compliance check
    check = db.query(ComplianceCheck).filter(
        ComplianceCheck.submission_id == submission_id
    ).order_by(ComplianceCheck.checked_at.desc()).first()

    if not check:
        return {
            "submission_id": submission_id,
            "status": submission.status,
            "message": "No compliance check found. Run analysis first."
        }

    violations = db.query(Violation).filter(
        Violation.compliance_check_id == check.id
    ).all()

    return {
        "submission_id": submission_id,
        "check_id": str(check.id),
        "overall_score": check.overall_score,
        "grade": check.grade,
        "compliance_status": check.status,
        "scores": check.scores,
        "checked_at": check.checked_at.isoformat() if check.checked_at else None,
        "violations": [_serialize_violation(v) for v in violations],
        "violation_count": len(violations)
    }


@router.get("/check/{check_id}")
async def get_compliance_check(
    check_id: str,
    db: Session = Depends(get_db)
):
    """Get details of a specific compliance check by ID."""
    summary = await ComplianceEngine.get_check_summary(check_id, db)
    if not summary:
        raise HTTPException(status_code=404, detail="Compliance check not found")
    return summary


# --------------------------------------------------------------------------
# Adaptive rule weights (HITL feedback)
# --------------------------------------------------------------------------

class ViolationFeedbackRequest(BaseModel):
    """Reviewer verdict on one finding — the learning signal."""
    verdict: Literal["accept", "reject"]
    severity_override: Optional[
        Literal["critical", "high", "medium", "low", "moderate", "informational"]
    ] = None
    comment: Optional[str] = None


class ReviewerScoreRequest(BaseModel):
    """Reviewer's own document score — held-out evaluation, never trained on."""
    score: float = Field(..., ge=0.0, le=100.0)


@router.post("/violations/{violation_id}/feedback")
async def submit_violation_feedback(
    violation_id: str,
    payload: ViolationFeedbackRequest,
    db: Session = Depends(get_db),
):
    """Record a reviewer's accept/reject on a finding and update the fired
    rule's learned reliability (Beta-Binomial pseudo-counts, damped by the
    prior). Re-submitting flips the stored verdict without double-counting.
    """
    from app.services.rule_feedback_service import RuleFeedbackService

    try:
        return RuleFeedbackService.apply_feedback(
            db,
            violation_id,
            payload.verdict,
            severity_override=payload.severity_override,
            comment=payload.comment,
        )
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/check/{check_id}/reviewer-score")
async def submit_reviewer_score(
    check_id: str,
    payload: ReviewerScoreRequest,
    db: Session = Depends(get_db),
):
    """Log the reviewer's document-level score next to the system's.

    Evaluation-only by design: this value never feeds scoring or weight
    updates (training on the evaluation metric would Goodhart it). The
    |system − reviewer| gap over time is the convergence curve that shows
    whether the adaptive weights actually improve the system.
    """
    check = db.query(ComplianceCheck).filter(ComplianceCheck.id == check_id).first()
    if not check:
        raise HTTPException(status_code=404, detail="Compliance check not found")

    check.reviewer_score = payload.score
    check.reviewer_scored_at = datetime.utcnow()
    db.commit()

    system_score = check.overall_score
    return {
        "check_id": str(check.id),
        "reviewer_score": payload.score,
        "system_score": system_score,
        "gap": abs(system_score - payload.score) if system_score is not None else None,
    }
