"""
Compliance Analysis Routes

Endpoints:
- POST /compliance/analyze/{submission_id} - Trigger compliance analysis
- GET  /compliance/results/{submission_id}  - Get analysis results
- GET  /compliance/check/{check_id}         - Get specific check details
- POST /compliance/resume/{submission_id}   - Resume HITL workflow
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models.submission import Submission
from app.models.compliance_check import ComplianceCheck
from app.models.violation import Violation
from app.services.agents.compliance.engine import ComplianceEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/compliance", tags=["Compliance Analysis"])


@router.post("/analyze/{submission_id}")
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


@router.post("/analyze/{submission_id}/sync")
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
            # HITL pause
            return {
                "status": "waiting_for_review",
                "submission_id": submission_id,
                "message": "Analysis paused for human review"
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
        logger.error(f"Sync analysis failed for {submission_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


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
                "chunk_index": v.chunk_index
            }
            for v in violations
        ],
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


@router.post("/resume/{submission_id}")
async def resume_compliance_review(
    submission_id: str,
    feedback: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Resume a HITL-paused compliance workflow with optional feedback.
    """
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    if submission.status != "waiting_for_review":
        raise HTTPException(
            status_code=400,
            detail=f"Submission is not waiting for review. Current status: {submission.status}"
        )

    try:
        from app.services.agents.orchestrator import orchestrator
        from app.services.agents.graph.context import GraphContext

        token = GraphContext.set_db_session(db)
        config = {"configurable": {"thread_id": str(submission_id)}}

        final_state = await orchestrator.resume_workflow(config, feedback=feedback)

        if final_state is None:
            return {"status": "still_paused", "message": "Workflow paused again for further review"}

        snapshot = await orchestrator.get_state(config)
        if snapshot.next:
            return {"status": "still_paused", "message": "Workflow paused again"}

        # Persist results
        check = ComplianceEngine.persist_results(
            submission_id=str(submission_id),
            violations=final_state.get("violations", []),
            scores=final_state.get("scores", {}),
            db=db
        )
        submission.status = "analyzed"
        db.commit()

        return {
            "status": "completed",
            "check_id": str(check.id),
            "overall_score": check.overall_score,
            "grade": check.grade
        }
    except Exception as e:
        logger.error(f"Resume failed for {submission_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Resume failed: {str(e)}")
