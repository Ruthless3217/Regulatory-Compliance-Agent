import logging
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.analysis_run import AnalysisRun
from app.models.llm_usage_event import LlmUsageEvent
from app.services.observability import audit
from datetime import datetime, timezone
import asyncio

logger = logging.getLogger(__name__)

async def open_run(db: Session, submission_id: str, user, session_id: str, trigger_source: str = "api") -> AnalysisRun:
    run_number = db.query(func.count(AnalysisRun.id)).filter(AnalysisRun.submission_id == submission_id).scalar() or 0
    run_number += 1
    
    run = AnalysisRun(
        submission_id=submission_id,
        triggered_by=str(user.id) if user else None,
        session_id=session_id,
        run_number=run_number,
        is_rerun=(run_number > 1),
        trigger_source=trigger_source,
        status="running"
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    
    event_type = "analysis_rerun" if run.is_rerun else "analysis_started"
    # Fire and forget audit
    asyncio.create_task(audit.record(event_type, actor=user, target_type="submission", target_id=submission_id, metadata={"run_id": str(run.id)}))
    return run

async def close_run(db: Session, run: AnalysisRun, final_state: dict, user=None):
    status = final_state.get("status", "failed")
    
    # Calculate rollup from llm_usage_events
    rollup = db.query(
        func.sum(LlmUsageEvent.prompt_tokens).label("prompt"),
        func.sum(LlmUsageEvent.completion_tokens).label("completion"),
        func.sum(LlmUsageEvent.total_cost_usd).label("cost")
    ).filter(LlmUsageEvent.run_id == str(run.id)).first()
    
    run.status = status
    run.finished_at = datetime.now(timezone.utc)
    if run.started_at:
        run.duration_ms = int((run.finished_at.replace(tzinfo=None) - run.started_at.replace(tzinfo=None)).total_seconds() * 1000)
        
    run.prompt_tokens = int(rollup.prompt or 0)
    run.completion_tokens = int(rollup.completion or 0)
    run.total_tokens = run.prompt_tokens + run.completion_tokens
    run.total_cost_usd = float(rollup.cost or 0.0)
    
    if "check_id" in final_state and final_state["check_id"]:
        run.compliance_check_id = final_state["check_id"]

    # Durable retrieval/observability extract (migration 0022): why candidates
    # entered or were refused from context, grounding mix, degradation flags.
    if final_state.get("run_metadata"):
        run.run_metadata = final_state["run_metadata"]

    run.degraded_reason = final_state.get("error") if status == "failed" else None

    db.commit()
    
    asyncio.create_task(audit.record("analysis_finished", actor=user, target_type="submission", target_id=str(run.submission_id), metadata={"run_id": str(run.id), "status": status, "cost": float(run.total_cost_usd or 0)}))
