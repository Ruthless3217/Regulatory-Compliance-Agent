"""
Compliance Analysis Routes

Endpoints:
- POST /compliance/analyze/{submission_id}        - Trigger compliance analysis (background)
- POST /compliance/analyze/{submission_id}/sync   - Synchronous analysis
- POST /compliance/analyze/{submission_id}/stream - SSE-streamed analysis with progress
- GET  /compliance/results/{submission_id}        - Get analysis results
- GET  /compliance/check/{check_id}               - Get specific check details
- POST /compliance/violations/{violation_id}/feedback - Reviewer verdict (back-compat accept/reject shim)
- POST /compliance/violations/{violation_id}/actions  - Reviewer action taxonomy (correct/not_violation/dismiss)
- GET  /compliance/reviewer-actions/queues            - Open reviewer-action queue entries (feedback:review)
- POST /compliance/reviewer-actions/{feedback_id}/resolve - Resolve a queued reviewer-action entry
- GET  /compliance/submissions/{submission_id}/runs   - Reviewer-facing run history
- GET  /compliance/runs/{run_id}/diff                 - Diff one run's violations against another
- POST /compliance/check/{check_id}/reviewer-score    - Held-out reviewer score (eval only)
"""
import asyncio
import json
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Dict, Literal, Optional, Set

from app.api.rate_limit import llm_rate_limit
from app.services.llm_budget import llm_budget_guard
from app.database import get_db, SessionLocal
from app.models.submission import Submission
from app.models.compliance_check import ComplianceCheck
from app.models.violation import Violation
from app.models.analysis_run import AnalysisRun
from app.models.rule_feedback import RuleFeedback
from app.services.agents.compliance.engine import ComplianceEngine
from app.services.violation_serializer import serialize_violation, latest_feedback_map
from app.auth.dependencies import require

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/compliance", tags=["Compliance Analysis"])


@router.post("/analyze/{submission_id}", dependencies=[Depends(llm_rate_limit), Depends(llm_budget_guard)])
async def analyze_submission(
    submission_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    user: dict = Depends(require("analysis:run")),
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
        from app.config import settings
        from app.services.run_tracker import find_stale_running_run

        stale_run = await find_stale_running_run(db, submission_id, settings.stale_analysis_run_minutes)
        if stale_run is None:
            return {"message": "Analysis already in progress", "submission_id": submission_id}
        # Stale/orphaned run (owning process was killed mid-flight): fall
        # through and queue a new attempt. ComplianceEngine.analyze_submission's
        # own guard performs the actual reclaim under the row lock.
        logger.warning(f"Submission {submission_id} has a stale 'analyzing' run; allowing reclaim.")

    # Queue analysis as a background task
    session_id = getattr(getattr(request, 'state', None), 'session_id', None)
    background_tasks.add_task(_run_analysis, submission_id, user, session_id)

    return {
        "message": "Compliance analysis started",
        "submission_id": submission_id,
        "status": "analyzing"
    }


async def _run_analysis(submission_id: str, user=None, session_id=None):
    """Background task runner for compliance analysis."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        await ComplianceEngine.analyze_submission(submission_id, db, user=user, session_id=session_id)
        logger.info(f"Background analysis completed for {submission_id}")
    except Exception as e:
        logger.error(f"Background analysis failed for {submission_id}: {e}")
    finally:
        db.close()


@router.post("/analyze/{submission_id}/sync", dependencies=[Depends(llm_rate_limit), Depends(llm_budget_guard)])
async def analyze_submission_sync(
    submission_id: str,
    request: Request,
    user: dict = Depends(require("analysis:run")),
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
        session_id = getattr(getattr(request, 'state', None), 'session_id', None)
        compliance_check = await ComplianceEngine.analyze_submission(submission_id, db, user=user, session_id=session_id)

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


async def _analyze_and_stream(submission_id: str, user=None, session_id=None):
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
            await ComplianceEngine.analyze_submission(submission_id, db, user=user, session_id=session_id)
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
                            # Freshly-created violations mid-run have no reviewer
                            # feedback yet — no bulk lookup needed here.
                            by_chunk.setdefault(v.chunk_index or 0, []).append(serialize_violation(v))
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


@router.post("/analyze/{submission_id}/stream", dependencies=[Depends(llm_rate_limit), Depends(llm_budget_guard)])
async def analyze_submission_stream(
    submission_id: str,
    request: Request,
    user: dict = Depends(require("analysis:run"))
):
    """SSE-stream analysis progress: stage / chunk / score / done / error."""
    session_id = getattr(getattr(request, 'state', None), 'session_id', None)
    return StreamingResponse(
        _analyze_and_stream(submission_id, user=user, session_id=session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/results/{submission_id}")
async def get_compliance_results(
    submission_id: str,
    user: dict = Depends(require("submission:read")),
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
    feedback_map = latest_feedback_map(db, [v.id for v in violations])

    return {
        "submission_id": submission_id,
        "check_id": str(check.id),
        "overall_score": check.overall_score,
        "grade": check.grade,
        "compliance_status": check.status,
        "scores": check.scores,
        "checked_at": check.checked_at.isoformat() if check.checked_at else None,
        "violations": [serialize_violation(v, feedback_map.get(str(v.id))) for v in violations],
        "violation_count": len(violations)
    }


@router.get("/check/{check_id}")
async def get_compliance_check(
    check_id: str,
    user: dict = Depends(require("submission:read")),
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
    user: dict = Depends(require("feedback:submit")),
    db: Session = Depends(get_db),
):
    """Record a reviewer's accept/reject on a finding and update the fired
    rule's learned reliability (Beta-Binomial pseudo-counts, damped by the
    prior). Re-submitting flips the stored verdict without double-counting.
    """
    from app.services.rule_feedback_service import RuleFeedbackService

    try:
        res = RuleFeedbackService.apply_feedback(
            db,
            violation_id,
            payload.verdict,
            severity_override=payload.severity_override,
            comment=payload.comment,
        )
        import asyncio
        from app.services.observability import audit
        asyncio.create_task(audit.record("feedback_submitted", actor=user, target_type="violation", target_id=violation_id, metadata=payload.model_dump()))
        return res
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


# --------------------------------------------------------------------------
# Reviewer-action taxonomy (Correct / Not-a-violation / Dismiss)
# --------------------------------------------------------------------------

# Which reviewer-supplied `reason` escalates a finding to a human-review
# queue, and which queue. Reasons absent from this map (or no reason at all)
# route to no queue — most actions are routine and need no escalation.
REASON_TO_QUEUE: Dict[str, str] = {
    "wrong_severity": "needs_severity_review",
    "out_of_scope": "needs_legal_review",
    "duplicate": "needs_dedup_review",
}


def resolve_routed_queue(reason: Optional[str]) -> Optional[str]:
    """Pure REASON_TO_QUEUE lookup — no queue for an unmapped/absent reason."""
    if not reason:
        return None
    return REASON_TO_QUEUE.get(reason)


class ViolationActionRequest(BaseModel):
    """The real reviewer-action taxonomy replacing the binary accept/reject
    shim: Correct (the finding is right), Not-a-violation (it's wrong), or
    Dismiss (skip it, no weight-update signal either way)."""
    action: Literal["correct", "not_violation", "dismiss"]
    reason: Optional[str] = None
    explanation: Optional[str] = None
    final_text: Optional[str] = None
    severity_override: Optional[
        Literal["critical", "high", "medium", "low", "moderate", "informational"]
    ] = None


@router.post("/violations/{violation_id}/actions")
async def submit_violation_action(
    violation_id: str,
    payload: ViolationActionRequest,
    user: dict = Depends(require("feedback:submit")),
    db: Session = Depends(get_db),
):
    """Record a reviewer's Correct/Not-a-violation/Dismiss action.

    Correct/not_violation delegate the rule-weight update to
    `RuleFeedbackService.apply_feedback` unchanged (mapped to its
    accept/reject vocabulary); dismiss skips weight update entirely. Either
    way this is a real, server-persisted action — previously "Dismiss" was
    100% client-side React state that persisted nothing (silent-discard bug).
    """
    from app.services.rule_feedback_service import RuleFeedbackService

    try:
        res = RuleFeedbackService.apply_action(
            db,
            violation_id,
            payload.action,
            reviewer_id=getattr(user, "id", None),
            reason=payload.reason,
            explanation=payload.explanation,
            final_text=payload.final_text,
            severity_override=payload.severity_override,
            routed_queue=resolve_routed_queue(payload.reason),
        )
        import asyncio
        from app.services.observability import audit
        asyncio.create_task(audit.record("violation_action_submitted", actor=user, target_type="violation", target_id=violation_id, metadata=payload.model_dump()))
        return res
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


class ReviewerActionResolveRequest(BaseModel):
    note: Optional[str] = None


@router.get("/reviewer-actions/queues")
async def list_reviewer_action_queues(
    queue: Optional[str] = Query(None),
    min_occurrences: int = Query(1, ge=1),
    user: dict = Depends(require("feedback:review")),
    db: Session = Depends(get_db),
):
    """Open (unresolved) reviewer-action queue entries — optionally filtered
    to one named queue, and to only the patterns (same rule_id + reason) that
    recurred at least `min_occurrences` times, so a one-off doesn't drown out
    a systemic issue.
    """
    q = db.query(RuleFeedback).filter(
        RuleFeedback.routed_queue.isnot(None),
        RuleFeedback.queue_resolved_at.is_(None),
    )
    if queue:
        q = q.filter(RuleFeedback.routed_queue == queue)
    rows = q.order_by(RuleFeedback.created_at.desc()).all()

    def _pattern(r) -> tuple:
        return (str(r.rule_id) if r.rule_id else None, r.reason)

    counts: Dict[tuple, int] = {}
    for r in rows:
        key = _pattern(r)
        counts[key] = counts.get(key, 0) + 1

    entries = [r for r in rows if counts[_pattern(r)] >= min_occurrences]

    return {
        "queue": queue,
        "min_occurrences": min_occurrences,
        "entries": [
            {
                "id": str(r.id),
                "violation_id": str(r.violation_id),
                "rule_id": str(r.rule_id) if r.rule_id else None,
                "verdict": r.verdict,
                "reason": r.reason,
                "comment": r.comment,
                "routed_queue": r.routed_queue,
                "occurrences": counts[_pattern(r)],
                "submission_id": str(r.submission_id) if r.submission_id else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in entries
        ],
    }


@router.post("/reviewer-actions/{feedback_id}/resolve")
async def resolve_reviewer_action_queue_entry(
    feedback_id: str,
    payload: ReviewerActionResolveRequest,
    user: dict = Depends(require("feedback:review")),
    db: Session = Depends(get_db),
):
    """Mark one queued reviewer-action entry resolved (e.g. a severity review
    or legal review that's been triaged)."""
    entry = db.query(RuleFeedback).filter(RuleFeedback.id == feedback_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Reviewer-action queue entry not found")
    if entry.routed_queue is None:
        raise HTTPException(status_code=400, detail="Entry is not routed to any queue")

    entry.queue_resolved_at = datetime.utcnow()
    entry.queue_resolved_by = getattr(user, "id", None)
    entry.queue_resolved_note = payload.note
    db.commit()

    return {
        "id": str(entry.id),
        "routed_queue": entry.routed_queue,
        "queue_resolved_at": entry.queue_resolved_at.isoformat(),
        "queue_resolved_by": str(entry.queue_resolved_by) if entry.queue_resolved_by else None,
        "queue_resolved_note": entry.queue_resolved_note,
    }


# --------------------------------------------------------------------------
# Reviewer-facing run history + diff
#
# Distinct from the existing admin-only GET /super_admin/submissions/{id}/runs
# (gated behind usage:view, keeps cost/duration) — this surfaces only what a
# reviewer needs (run_number, status, timing) behind submission:read.
# --------------------------------------------------------------------------

def _run_summary(r: AnalysisRun) -> dict:
    return {
        "id": str(r.id),
        "run_number": r.run_number,
        "is_rerun": bool(r.is_rerun),
        "status": r.status,
        "degraded_reason": r.degraded_reason,
        "compliance_check_id": str(r.compliance_check_id) if r.compliance_check_id else None,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "scoring_policy_version": r.scoring_policy_version,
    }


@router.get("/submissions/{submission_id}/runs")
async def list_submission_runs(
    submission_id: str,
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db),
):
    """Reviewer-facing run history for a submission, oldest to newest."""
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    runs = (
        db.query(AnalysisRun)
        .filter(AnalysisRun.submission_id == submission_id)
        .order_by(AnalysisRun.run_number)
        .all()
    )
    return {"submission_id": submission_id, "runs": [_run_summary(r) for r in runs]}


@router.get("/runs/{run_id}/diff")
async def diff_run(
    run_id: str,
    against: str = Query("previous"),
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db),
):
    """Diff one run's violations against `previous` (the prior run_number for
    the same submission) or an explicit run_id. Findings are matched across
    runs by (rule_id, category, chunk_index, description) — each run's
    violation rows are freshly created, so there's no stable id to join on.
    """
    run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Analysis run not found")

    if against == "previous":
        against_run = (
            db.query(AnalysisRun)
            .filter(
                AnalysisRun.submission_id == run.submission_id,
                AnalysisRun.run_number < run.run_number,
            )
            .order_by(AnalysisRun.run_number.desc())
            .first()
        )
    else:
        against_run = db.query(AnalysisRun).filter(AnalysisRun.id == against).first()
        if against_run and str(against_run.submission_id) != str(run.submission_id):
            raise HTTPException(status_code=400, detail="Runs belong to different submissions")

    if against_run is None:
        raise HTTPException(status_code=404, detail="No run to diff against")

    def _violations_for(r: AnalysisRun):
        if not r.compliance_check_id:
            return []
        return db.query(Violation).filter(Violation.compliance_check_id == r.compliance_check_id).all()

    current_violations = _violations_for(run)
    prior_violations = _violations_for(against_run)

    def _key(v: Violation) -> tuple:
        return (str(v.rule_id) if v.rule_id else None, v.category, v.chunk_index, v.description)

    current_by_key = {_key(v): v for v in current_violations}
    prior_by_key = {_key(v): v for v in prior_violations}

    feedback_map = latest_feedback_map(db, [v.id for v in current_violations + prior_violations])

    added = [
        serialize_violation(v, feedback_map.get(str(v.id)))
        for k, v in current_by_key.items() if k not in prior_by_key
    ]
    removed = [
        serialize_violation(v, feedback_map.get(str(v.id)))
        for k, v in prior_by_key.items() if k not in current_by_key
    ]
    unchanged_count = len(set(current_by_key) & set(prior_by_key))

    return {
        "run_id": str(run.id),
        "against_run_id": str(against_run.id),
        "run_number": run.run_number,
        "against_run_number": against_run.run_number,
        "added": added,
        "removed": removed,
        "unchanged_count": unchanged_count,
        "summary": {"added": len(added), "removed": len(removed), "unchanged": unchanged_count},
    }


@router.post("/check/{check_id}/reviewer-score")
async def submit_reviewer_score(
    check_id: str,
    payload: ReviewerScoreRequest,
    user: dict = Depends(require("feedback:submit")),
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
