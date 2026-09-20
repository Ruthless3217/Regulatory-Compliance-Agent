"""
Compliance Analysis Routes

Endpoints:
- POST /compliance/analyze/{submission_id}        - Trigger compliance analysis (background)
- POST /compliance/analyze/{submission_id}/sync   - Synchronous analysis
- POST /compliance/analyze/{submission_id}/stream - SSE-streamed analysis with progress
- POST /compliance/analyze/{submission_id}/scoped - Partial re-run over named sections/chunks
- GET  /compliance/submissions/{submission_id}/scopes - Section titles a scoped run can name
- GET  /compliance/results/{submission_id}        - Get analysis results
- GET  /compliance/check/{check_id}               - Get specific check details
- POST /compliance/violations/{violation_id}/feedback - Reviewer verdict (back-compat accept/reject shim)
- POST /compliance/violations/{violation_id}/actions  - Reviewer action taxonomy (correct/not_violation/dismiss)
- POST /compliance/submissions/{submission_id}/violations - Reviewer-authored flag on text the model missed
- DELETE /compliance/violations/{violation_id}           - Delete a reviewer-authored flag (never a model one)
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
from fastapi import APIRouter, Body, Depends, HTTPException, BackgroundTasks, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Dict, List, Literal, Optional, Set

from app.api.rate_limit import llm_rate_limit
from app.services.llm_budget import llm_budget_guard
from app.services import export_common
from app.models.rule import Rule
from app.services.llm_service import LLMUnavailableError, chat_llm_service
from app.services.observability.tracing import trace_root
from app.database import get_db, SessionLocal
from app.models.submission import Submission
from app.models.compliance_check import ComplianceCheck
from app.models.violation import Violation
from app.models.analysis_run import AnalysisRun
from app.models.rule_feedback import RuleFeedback


def _submission_id_for_violation(db, violation_id):
    """The document a finding belongs to, for `audit_events.scope_submission_id`.

    A violation event is part of its document's trail, but the routes that act
    on findings only ever see a violation id. Resolved through the finding's
    compliance check. Returns None rather than raising — a trail row with no
    scope is worth more than a 500 on the action itself.
    """
    violation = db.query(Violation).filter(Violation.id == violation_id).first()
    if violation is None:
        return None
    check = (
        db.query(ComplianceCheck)
        .filter(ComplianceCheck.id == violation.compliance_check_id)
        .first()
    )
    return getattr(check, "submission_id", None)
from app.services.agents.compliance.engine import ComplianceEngine
from app.services import analysis_warnings as _aw
from app.services.violation_serializer import (
    finding_counts,
    latest_feedback_map,
    serialize_violation,
)
from app.auth.dependencies import require
from app.auth.visibility import get_visible_submission
from app.auth.permissions import role_has

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
    submission = get_visible_submission(db, submission_id, user)

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
    submission = get_visible_submission(db, submission_id, user)

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


# --------------------------------------------------------------------------
# Scoped (partial) re-analysis
#
# A reviewer who edited two paragraphs re-checks those two paragraphs. The hour
# of review already spent on the rest of the document is not currency to pay for
# it. From the product design, and not negotiable:
#
#   "A partial re-run cannot produce a document score, and does not pretend to.
#    Approval requires a whole-document run."
# --------------------------------------------------------------------------

class ScopedAnalyzeRequest(BaseModel):
    section_titles: Optional[List[str]] = None   # re-run only these sections
    chunk_indexes: Optional[List[int]] = None    # or only these chunks


@router.post("/analyze/{submission_id}/scoped", dependencies=[Depends(llm_rate_limit), Depends(llm_budget_guard)])
async def analyze_submission_scoped(
    submission_id: str,
    payload: ScopedAnalyzeRequest,
    request: Request,
    user: dict = Depends(require("analysis:run")),
    db: Session = Depends(get_db),
):
    """Re-check only part of a document, keeping the review already recorded on
    the rest.

    Findings INSIDE the scope are replaced by this run's findings. Findings
    OUTSIDE it survive as the same rows — same `violations.id`, so every
    rule_feedback verdict keyed on them still resolves, and same
    `review_status`. They are re-parented onto the new check rather than
    copied: a copy would be a new id and an orphaned verdict.

    The resulting check carries NO overall_score and NO grade. Part of a
    document cannot be graded as the document, so the number is left NULL
    instead of being computed over a fragment, and the run itself is stamped
    `scoped: true` + its scope so no later reader can mistake it for a full one.

    ponytail: the analysis pass itself is still whole-document — the scope is
    applied to the RESULTS, not to the LLM work. Narrowing the work needs the
    chunk filter plumbed through the orchestrator/graph nodes; the reviewer-
    visible contract (verdicts kept, no partial grade) does not depend on it.
    """
    if not payload.section_titles and not payload.chunk_indexes:
        raise HTTPException(
            status_code=400,
            detail=(
                "A scoped re-analysis needs a scope: give section_titles and/or "
                "chunk_indexes. A scoped run with no scope is a full run, and "
                "must be requested as one explicitly via "
                "POST /compliance/analyze/{submission_id}/sync."
            ),
        )

    submission = get_visible_submission(db, submission_id, user)

    prior_check = (
        db.query(ComplianceCheck)
        .filter(ComplianceCheck.submission_id == submission_id)
        .order_by(ComplianceCheck.checked_at.desc())
        .first()
    )
    if prior_check is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "This submission has not been analysed yet, so there is no review "
                "to preserve and nothing to re-check in part. Run a full analysis "
                "first."
            ),
        )

    titles = set(payload.section_titles or ())
    indexes = set(payload.chunk_indexes or ())

    def _in_scope(v: Violation) -> bool:
        return v.section_title in titles or v.chunk_index in indexes

    # Snapshot the out-of-scope rows BEFORE the run — these are the ones the
    # reviewer already ruled on, and the ones this run may not spend.
    preserved = [
        v
        for v in db.query(Violation).filter(Violation.compliance_check_id == prior_check.id).all()
        if not _in_scope(v)
    ]

    session_id = getattr(getattr(request, "state", None), "session_id", None)
    try:
        check = await ComplianceEngine.analyze_submission(
            submission_id, db, user=user, session_id=session_id
        )
    except Exception as e:
        logger.error(f"Scoped analysis failed for {submission_id}: {e}")
        raise HTTPException(status_code=500, detail="Analysis failed; see server logs.")

    if check is None:
        # Degraded ('needs_review') or hard failure ('failed'): nothing was
        # persisted, so nothing was replaced — and nothing may be discarded.
        db.refresh(submission)
        return {
            "status": submission.status,
            "submission_id": submission_id,
            "scoped": True,
            "message": (
                "Scoped re-analysis could not be graded; the existing findings "
                "and reviewer verdicts are unchanged."
            ),
        }

    # Keep only the in-scope half of the fresh run, then re-parent the reviewed
    # half of the old one onto it.
    replaced = 0
    for v in db.query(Violation).filter(Violation.compliance_check_id == check.id).all():
        if _in_scope(v):
            replaced += 1
        else:
            db.delete(v)
    for v in preserved:
        v.compliance_check_id = check.id
        db.add(v)

    # "A partial re-run cannot produce a document score, and does not pretend
    # to." The engine scores every run it persists; a scoped run gives that
    # number back rather than passing a fragment off as the document.
    check.overall_score = None
    check.grade = None

    scope = {"section_titles": payload.section_titles, "chunk_indexes": payload.chunk_indexes}
    run = (
        db.query(AnalysisRun)
        .filter(AnalysisRun.compliance_check_id == check.id)
        .order_by(AnalysisRun.run_number.desc())
        .first()
    )
    if run is not None:
        # Rebound, not mutated in place: plain JSONB is not change-tracked.
        run.run_metadata = {**(run.run_metadata or {}), "scoped": True, "scope": scope}
        db.add(run)

    db.commit()

    return {
        "status": "completed",
        "submission_id": submission_id,
        "check_id": str(check.id),
        "scoped": True,
        "scope": scope,
        # Explicitly null, and explicitly so in the response: approval requires
        # a whole-document run.
        "overall_score": None,
        "grade": None,
        "replaced_count": replaced,
        "preserved_count": len(preserved),
        "message": (
            f"Partial re-analysis: {replaced} finding(s) replaced inside the scope, "
            f"{len(preserved)} left untouched outside it. This run is not a document "
            f"grade — approval requires a whole-document run."
        ),
    }


@router.get("/submissions/{submission_id}/scopes")
async def list_submission_scopes(
    submission_id: str,
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db),
):
    """The section titles a scoped re-analysis can actually name, with the
    finding count in each — so a UI offers real scopes instead of free text."""
    submission = get_visible_submission(db, submission_id, user)

    check = (
        db.query(ComplianceCheck)
        .filter(ComplianceCheck.submission_id == submission_id)
        .order_by(ComplianceCheck.checked_at.desc())
        .first()
    )
    if check is None:
        return {"submission_id": submission_id, "check_id": None, "scopes": [], "untitled_count": 0}

    counts: Dict[str, int] = {}
    untitled = 0
    for v in db.query(Violation).filter(Violation.compliance_check_id == check.id).all():
        if v.section_title:
            counts[v.section_title] = counts.get(v.section_title, 0) + 1
        else:
            untitled += 1

    return {
        "submission_id": submission_id,
        "check_id": str(check.id),
        "scopes": [
            {"section_title": t, "count": n}
            for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        # Findings with no section title can't be named as a scope — target
        # those by chunk_index instead.
        "untitled_count": untitled,
    }


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
        # visibility: exempt - runs after the response on its own session, with
        # no request context and no user to check. The route that spawns this
        # generator carries the guard.
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
                # visibility: exempt - polling tick inside the same post-response
                # generator; see the note on the initial lookup above.
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


# Why a completed run refused to produce a grade, in the reviewer's terms. The
# next action differs completely between these, and `degraded_reason` on its own
# is an internal token. Keys track ComplianceEngine._NEEDS_REVIEW_REASONS.
_REFUSAL_EXPLANATIONS = {
    "product_unresolved": (
        "the submission's regulatory scope could not be established from the "
        "declared product line and the products found in the document"
    ),
    "no_grounded_evidence": (
        "no applicable rule, precedent or product fact card survived for this "
        "document, so there was nothing authoritative to grade it against"
    ),
    "product_ambiguous": (
        "a product identifier in the document matches more than one fact card, "
        "so grading it would mean picking one variant arbitrarily"
    ),
    "product_resolution_failed": "product resolution itself errored",
    "scope_metadata_missing": (
        "retrieved rules or precedents carry no product scope, so their "
        "applicability could not be established"
    ),
    "knowledge_base_empty": "the knowledge base returned nothing to check against",
    "rules_unavailable": "the rule corpus was unavailable",
    "disclosure_unavailable": "the required-disclosure corpus was unavailable",
    "disclosure_recall_degraded": (
        "the required-disclosure sweep covered only part of the document"
    ),
    "rag_degraded": "retrieval was degraded",
    "analysis_incomplete": "one or more sections failed to grade",
    "no_content": "no analyzable content could be extracted",
}


# What a run's named limitation means for the reviewer. A warning is NOT a
# refusal: the analysis reached a determination, and this says what that
# determination does not cover. Keys track graph/nodes._add_warning.
_WARNING_EXPLANATIONS = {
    "rider_uins_without_fact_cards": (
        "a rider or combination component named in this document has no "
        "authoritative record of its own, so its specific benefits and "
        "guardrails were not checked"
    ),
    "unknown_uins": (
        "a product identifier in this document is not in the product corpus, "
        "so that product's own obligations were not checked"
    ),
    "declared_products_without_fact_cards": (
        "a product named in this document is on the declared catalogue but has "
        "no authoritative record yet, so its own obligations were not checked"
    ),
    "edition_conflicts": (
        "this document names an edition or variant of a product that the "
        "corpus has no record of; the nearest known edition was NOT used in "
        "its place, so that product's own obligations were not checked"
    ),
    # Legacy single precedent code (runs persisted before the vocabulary split).
    "precedent_evidence_unavailable": (
        "no prior reviewer cases were available, so findings rest on rules and "
        "product facts alone"
    ),
    "precedent_tier_unavailable": (
        "the precedent tier could not be retrieved because a retrieval "
        "component failed; every section was still analysed against rules and "
        "product facts"
    ),
    "precedent_corpus_empty": (
        "the precedent corpus contains no cases yet, so findings rest on rules "
        "and product facts alone"
    ),
    "precedent_scope_metadata_incomplete": (
        "prior cases were retrieved for this document but carry no product "
        "scope and could not be proven applicable, so they were not used"
    ),
    "rule_scope_metadata_incomplete": (
        "some rules carry no product scope and could not be proven applicable, "
        "so they were not applied"
    ),
    "product_grounding_budget": (
        "more products were identified than the analysis prompt can carry; the "
        "regulatory scope still covers all of them"
    ),
    "retrieval_degraded": (
        "per-section retrieval failed and the analysis fell back to the flat "
        "rule set"
    ),
}


def _warnings_payload(run) -> list:
    """The run's warnings, each with a reviewer-facing explanation and its
    KIND (coverage / tier / infrastructure — app.services.analysis_warnings).

    The kind is what lets every surface say the right thing: only a coverage
    warning may claim the score covers less than the whole document. Runs
    persisted before kinds existed are classified by code here, so the
    frontend never has to guess."""
    from app.services import analysis_warnings as aw

    raw = ((getattr(run, "run_metadata", None) or {}).get("analysis_warnings") or [])
    out = []
    for warning in raw:
        if not isinstance(warning, dict):
            continue
        code = str(warning.get("code") or "")
        out.append({
            "code": code,
            "kind": aw.warning_kind(warning),
            "detail": warning.get("detail"),
            "explanation": _WARNING_EXPLANATIONS.get(code, ""),
        })
    return out


def _analysis_state(submission, latest_run) -> tuple:
    """(state, degraded_reason) for a submission that has no ComplianceCheck."""
    if latest_run is None:
        return "not_analyzed", None
    if latest_run.status == "running":
        return "analyzing", None
    if latest_run.status in {"needs_review", "failed"}:
        return latest_run.status, latest_run.degraded_reason
    # A run that closed 'completed' with no check is not a state the engine
    # produces; report the submission's own status rather than inventing one.
    return submission.status or "not_analyzed", latest_run.degraded_reason


def _analysis_state_message(state: str, reason: Optional[str]) -> str:
    if state == "not_analyzed":
        return "No compliance check found. Run analysis to grade this document."
    if state == "analyzing":
        return "Analysis is still running."
    explanation = _REFUSAL_EXPLANATIONS.get(reason or "")
    detail = f" — {explanation}" if explanation else ""
    if state == "needs_review":
        return (
            "Analysis completed but this document was NOT graded and needs "
            f"human review{detail}. No compliance score was recorded."
        )
    if state == "failed":
        return f"Analysis failed{detail}. No compliance score was recorded."
    return f"This document has not been graded{detail}."


@router.get("/results/{submission_id}")
async def get_compliance_results(
    submission_id: str,
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db)
):
    """Get the latest compliance analysis results for a submission."""
    submission = get_visible_submission(db, submission_id, user)

    # Get latest compliance check
    check = db.query(ComplianceCheck).filter(
        ComplianceCheck.submission_id == submission_id
    ).order_by(ComplianceCheck.checked_at.desc()).first()

    if not check:
        # No check can mean two very different things, and they used to read
        # identically: the document was never analysed, OR a run completed and
        # `evaluate_persistability` deliberately refused to grade it. Telling a
        # reviewer to "run analysis first" on a document the pipeline has
        # already judged un-gradeable hides the verdict behind an empty page.
        latest_run = (
            db.query(AnalysisRun)
            .filter(AnalysisRun.submission_id == submission_id)
            .order_by(AnalysisRun.run_number.desc())
            .first()
        )
        state, reason = _analysis_state(submission, latest_run)
        return {
            "submission_id": submission_id,
            "status": submission.status,
            "analysis_state": state,
            "degraded_reason": reason,
            "message": _analysis_state_message(state, reason),
        }

    violations = db.query(Violation).filter(
        Violation.compliance_check_id == check.id
    ).all()
    feedback_map = latest_feedback_map(db, [v.id for v in violations])
    counts = finding_counts(violations)

    # A graded run can still be a PARTIAL one: the scope was proven and the
    # findings are real, but some product's own record, the precedent corpus or
    # a rule's scope tag was missing. That must not read as a clean pass — the
    # verdict is already capped below "passed" (engine.cap_status_for_warnings)
    # and these name exactly what the grade does not cover.
    graded_run = (
        db.query(AnalysisRun)
        .filter(AnalysisRun.compliance_check_id == check.id)
        .order_by(AnalysisRun.run_number.desc())
        .first()
    )
    warnings = _warnings_payload(graded_run) if graded_run else []

    return {
        "submission_id": submission_id,
        "check_id": str(check.id),
        "analysis_state": "completed_with_warnings" if warnings else "completed",
        # Separate from the state above on purpose: a tier or infrastructure
        # warning makes the run "with warnings" but does NOT mean part of the
        # document went ungraded. Only a coverage warning does.
        "evidence_coverage": _aw.evidence_coverage_state(warnings),
        "limitation_statement": _aw.limitation_statement(warnings),
        "analysis_warnings": warnings,
        "overall_score": check.overall_score,
        "grade": check.grade,
        "compliance_status": check.status,
        "scores": check.scores,
        "checked_at": check.checked_at.isoformat() if check.checked_at else None,
        "violations": [serialize_violation(v, feedback_map.get(str(v.id))) for v in violations],
        # Back-compatible name now has one explicit meaning: findings that
        # affect the score. The persisted human-review lane is reported
        # separately instead of silently changing totals between surfaces.
        "violation_count": counts["scored"],
        "suppressed_count": counts["suppressed"],
        "reviewer_added_count": counts["reviewer_added"],
        "finding_count": counts["total"],
        "finding_counts": counts,
        # True once the document is edited after this analysis: the findings
        # below describe a superseded version, so the workspace blocks export
        # and prompts a re-run.
        "findings_stale": export_common.findings_are_stale(db, submission_id),
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

    The reviewer is attributed exactly as /actions does: rule_feedback is
    unique per (violation, reviewer), so submitting without one parked every
    verdict on the same NULL-reviewer row — one reviewer's accept silently
    flipped another's reject, reverting a pseudo-count that was never theirs.
    """
    from app.services.rule_feedback_service import RuleFeedbackService

    try:
        res = RuleFeedbackService.apply_feedback(
            db,
            violation_id,
            payload.verdict,
            reviewer_id=getattr(user, "id", None),
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
#
# Keys are the reviewer vocabulary the UI actually offers (ViolationCard's
# dismiss + not-a-violation reason lists), normalized to underscores. The UI
# sends them hyphenated ("wrong-severity"), which used to miss every key here
# and left needs_severity_review / needs_legal_review permanently empty.
# `out_of_scope` has no UI option left but is kept for rows written before this.
REASON_TO_QUEUE: Dict[str, str] = {
    "wrong_severity": "needs_severity_review",
    "needs_human_legal_review": "needs_legal_review",
    "valid_regulatory_exception": "needs_legal_review",
    "outdated_rule": "needs_legal_review",
    "out_of_scope": "needs_legal_review",
    "duplicate": "needs_dedup_review",
}


def normalize_reason(reason: Optional[str]) -> Optional[str]:
    """Reviewer reason keys as one vocabulary: the UI speaks kebab-case, the
    queue map and every stored row speak snake_case."""
    if not reason:
        return None
    return reason.strip().lower().replace("-", "_") or None


def resolve_routed_queue(reason: Optional[str]) -> Optional[str]:
    """Pure REASON_TO_QUEUE lookup — no queue for an unmapped/absent reason.
    Accepts either spelling of the key."""
    key = normalize_reason(reason)
    return REASON_TO_QUEUE.get(key) if key else None


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
    from app.services import rule_feedback_service as rfs
    from app.services.rule_feedback_service import RuleFeedbackService

    # Stored in the same spelling the queue map uses, so the queues endpoint's
    # (rule_id, reason) pattern count can't split one reason across two keys.
    reason = normalize_reason(payload.reason)

    try:
        res = RuleFeedbackService.apply_action(
            db,
            violation_id,
            payload.action,
            reviewer_id=getattr(user, "id", None),
            reason=reason,
            explanation=payload.explanation,
            final_text=payload.final_text,
            severity_override=payload.severity_override,
            routed_queue=resolve_routed_queue(reason),
        )
        # The verdict is now also a precedent: correct/not_violation teach the
        # "Reviewer feedback" corpus layer, dismiss un-teaches it. Fail-soft —
        # the feedback above is already committed and never blocks on this.
        await rfs.sync_reviewer_precedent(
            db,
            violation_id,
            payload.action,
            reason=reason,
            explanation=payload.explanation,
            final_text=payload.final_text,
        )
        import asyncio
        from app.services.observability import audit
        asyncio.create_task(audit.record(
            "violation_action_submitted", actor=user,
            target_type="violation", target_id=violation_id,
            scope_submission_id=_submission_id_for_violation(db, violation_id),
            metadata=payload.model_dump(),
        ))
        return res
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


# --------------------------------------------------------------------------
# Reviewer-authored findings (migration 0031)
#
# Lives here, not in submissions.py, because everything violation-shaped does:
# compliance.py already owns the Violation model, the serializer, the reviewer
# verdict/action endpoints, and the sibling /compliance/submissions/{id}/runs
# route. submissions.py owns document CONTENT (revisions/comments/export) and
# imports neither ComplianceCheck nor the serializer.
#
# Gated on `feedback:submit` — the scope every reviewer role already holds
# (user + admin + super_admin, see auth/permissions.py). Flagging text is the
# same act as judging a flag, so it needs no new scope.
# --------------------------------------------------------------------------

class ReviewerViolationCreate(BaseModel):
    """A finding a reviewer wrote by hand over text the model never flagged —
    the model's blind spots are otherwise the editor's blind spots too."""
    current_text: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    severity: Literal["critical", "high", "medium", "low", "moderate", "informational"]
    # Free-form on purpose: `violations.category` is String(50) and categories
    # come from the rule corpus at runtime, so an enum here would go stale.
    category: str = Field(..., min_length=1, max_length=50)
    suggested_fix: Optional[str] = None


@router.post("/submissions/{submission_id}/violations", status_code=201)
async def create_reviewer_violation(
    submission_id: str,
    payload: ReviewerViolationCreate,
    user: dict = Depends(require("feedback:submit")),
    db: Session = Depends(get_db),
):
    """Flag a span of a submission as an issue the model missed.

    Attaches to the submission's LATEST compliance check (violations.
    compliance_check_id is NOT NULL). If the submission has never been
    analysed there is no check to attach to and this 400s: fabricating a
    minimal ComplianceCheck would invent a graded record for a document nobody
    graded, and that phantom check would then be counted by the dashboard,
    funnel and calibration queries as if an analysis had happened.
    """
    submission = get_visible_submission(db, submission_id, user)

    check = (
        db.query(ComplianceCheck)
        .filter(ComplianceCheck.submission_id == submission_id)
        .order_by(ComplianceCheck.checked_at.desc())
        .first()
    )
    if check is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "This submission has not been analysed yet, so there is no "
                "compliance check to attach a finding to. Run the analysis "
                "first, then flag the text."
            ),
        )

    check_run = (
        db.query(AnalysisRun)
        .filter(AnalysisRun.compliance_check_id == check.id)
        .order_by(AnalysisRun.run_number.desc())
        .first()
    )

    violation = Violation(
        compliance_check_id=check.id,
        # No rule fired and no model produced this — both stay NULL/absent.
        rule_id=None,
        analysis_run_id=check_run.id if check_run else None,
        category=payload.category,
        severity=payload.severity,
        description=payload.description,
        current_text=payload.current_text,
        suggested_fix=payload.suggested_fix,
        auto_fixable="false",
        # NOT NULL with a 0.85 model default; a human assertion is not a model
        # probability, so record full certainty rather than a fake model score.
        confidence=1.0,
        source="reviewer",
        created_by=getattr(user, "id", None),
    )
    db.add(violation)
    db.commit()
    db.refresh(violation)

    from app.services.observability import audit
    asyncio.create_task(audit.record(
        "reviewer_violation_created", actor=user, target_type="violation",
        target_id=str(violation.id), scope_submission_id=submission.id,
        metadata={"submission_id": submission_id},
    ))
    return serialize_violation(violation)


@router.delete("/violations/{violation_id}")
async def delete_reviewer_violation(
    violation_id: str,
    user: dict = Depends(require("feedback:submit")),
    db: Session = Depends(get_db),
):
    """Delete a REVIEWER-authored flag — its author, or anyone holding
    `feedback:review` (admin/super_admin), may remove it.

    A model-authored violation is never deletable through this route: it is the
    evidence the model's own precision is measured against, and the reviewer
    already has non-destructive verdicts for it (Not-a-violation / Dismiss).
    """
    violation = db.query(Violation).filter(Violation.id == violation_id).first()
    if not violation:
        raise HTTPException(status_code=404, detail="Violation not found")

    if (violation.source or "model") != "reviewer":
        raise HTTPException(
            status_code=403,
            detail=(
                "Model-authored findings cannot be deleted. Record a "
                "'Not a violation' or 'Dismiss' verdict instead."
            ),
        )

    user_id = getattr(user, "id", None)
    is_author = violation.created_by is not None and str(violation.created_by) == str(user_id)
    if not is_author and not role_has(getattr(user, "role", ""), "feedback:review"):
        raise HTTPException(
            status_code=403,
            detail="Only the reviewer who created this flag, or an admin, can delete it.",
        )

    # Resolved before the delete — afterwards the row is gone and the trail
    # entry would have no document to hang off.
    scope_submission_id = _submission_id_for_violation(db, violation_id)

    db.delete(violation)
    db.commit()

    from app.services.observability import audit
    asyncio.create_task(audit.record(
        "reviewer_violation_deleted", actor=user, target_type="violation",
        target_id=violation_id, scope_submission_id=scope_submission_id,
    ))
    return {"message": "Violation deleted", "id": violation_id}


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
        # A partial run's check carries no score by design; surface WHY here so
        # the history can't be read as a full grade that happens to be missing.
        "scoped": bool((r.run_metadata or {}).get("scoped")),
        "scope": (r.run_metadata or {}).get("scope"),
    }


@router.get("/submissions/{submission_id}/runs")
async def list_submission_runs(
    submission_id: str,
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db),
):
    """Reviewer-facing run history for a submission, oldest to newest."""
    submission = get_visible_submission(db, submission_id, user)

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


# ---------------------------------------------------------------------------
# On-demand AI rewrite — proposes replacement wording for one flagged span.
# ---------------------------------------------------------------------------

class RewriteRequest(BaseModel):
    # Free-text reviewer steer ("keep it under 12 words", "keep the CTA").
    # Optional: with none, the model just re-addresses the finding.
    instruction: Optional[str] = Field(default=None, max_length=500)


_REWRITE_SYSTEM = (
    "You rewrite a flagged passage of Indian life-insurance marketing copy so it "
    "no longer violates the stated compliance finding.\n"
    "Rules:\n"
    "- Return ONLY the replacement passage. No preamble, quotes, or explanation.\n"
    "- Preserve the original meaning, tone, and approximate length. You are "
    "correcting a compliance defect, not rewriting the campaign.\n"
    "- Never introduce a new factual, numeric, guarantee, tax, or returns claim "
    "that is not already in the original passage.\n"
    "- If the passage cannot be made compliant without deleting the claim, return "
    "the passage with the offending claim removed rather than inventing a "
    "substitute."
)


def _trace_user_id(user) -> Optional[str]:
    """Reviewer identity for Langfuse ``user_id`` (username, else id)."""
    if user is None:
        return None
    return getattr(user, "username", None) or (str(user.id) if getattr(user, "id", None) else None)


def _violation_submission_id(violation) -> Optional[str]:
    """Submission a finding belongs to — the Langfuse session key, so a rewrite
    sits next to the analysis runs of the same document."""
    check = getattr(violation, "compliance_check", None)
    sid = getattr(check, "submission_id", None) if check is not None else None
    return str(sid) if sid else None


@router.post(
    "/violations/{violation_id}/rewrite",
    dependencies=[Depends(llm_rate_limit), Depends(llm_budget_guard)],
)
async def rewrite_violation_text(
    violation_id: str,
    payload: RewriteRequest = Body(default=RewriteRequest()),
    user: dict = Depends(require("submission:create")),
    db: Session = Depends(get_db),
):
    """Propose replacement wording for one finding. Writes nothing.

    Deliberately does NOT create a revision: every persisted edit invalidates
    the run's findings and forces a re-analysis before the document can be
    exported, so a rewrite the reviewer has not read yet must not cost them
    one. The caller applies the returned text through the normal revision
    path if they accept it.
    """
    violation = db.query(Violation).filter(Violation.id == violation_id).first()
    if not violation:
        raise HTTPException(status_code=404, detail="Violation not found")

    original = (violation.current_text or "").strip()
    if not original:
        raise HTTPException(
            status_code=422,
            detail="This finding has no quoted source text to rewrite",
        )

    rule_text = None
    if violation.rule_id:
        rule = db.query(Rule).filter(Rule.id == violation.rule_id).first()
        rule_text = rule.rule_text if rule else None

    parts = [f"Flagged passage:\n{original}", f"\nCompliance finding:\n{violation.description}"]
    if rule_text:
        parts.append(f"\nRule violated:\n{rule_text}")
    if violation.regulator_quote:
        parts.append(f"\nRegulator's own wording:\n{violation.regulator_quote}")
    if violation.suggested_fix:
        parts.append(f"\nEarlier suggestion (improve on it):\n{violation.suggested_fix}")
    if payload.instruction:
        parts.append(f"\nReviewer instruction (follow it):\n{payload.instruction.strip()}")

    # Own Langfuse trace, in the same session as the document's analysis runs.
    with trace_root(
        "rewrite-violation",
        as_type="chain",
        input={
            "violation_id": str(violation.id),
            "rule_id": str(violation.rule_id) if violation.rule_id else None,
            "original_text": original,
            "instruction": payload.instruction,
        },
        user_id=_trace_user_id(user),
        session_id=_violation_submission_id(violation),
        tags=["rewrite"],
        metadata={"violation_id": str(violation.id), "severity": violation.severity},
    ) as trace:
        try:
            proposed = await chat_llm_service.generate_response(
                prompt="\n".join(parts),
                system_prompt=_REWRITE_SYSTEM,
                temperature=0.2,
                purpose="rewrite",
            )
        except LLMUnavailableError as e:
            raise HTTPException(status_code=503, detail=f"Rewrite unavailable: {e}")

        proposed = (proposed or "").strip().strip('"').strip()
        if not proposed:
            raise HTTPException(status_code=502, detail="Model returned an empty rewrite")
        if trace is not None:
            trace.update(output={"proposed_text": proposed})

    return {
        "violation_id": str(violation.id),
        "original_text": original,
        "proposed_text": proposed,
        "instruction": payload.instruction,
    }
