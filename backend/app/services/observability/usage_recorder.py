import logging
from app.services.observability.usage_context import get_usage_context
from app.services.observability.cost import compute_cost
import asyncio

logger = logging.getLogger(__name__)

def _record_sync(model, provider, profile, prompt_tokens, completion_tokens, latency_ms, is_retry, token_source, ctx):
    from app.database import SessionLocal
    from app.models.llm_usage_event import LlmUsageEvent
    
    in_cost, out_cost, total = compute_cost(model, prompt_tokens, completion_tokens)
    
    db = SessionLocal()
    try:
        event = LlmUsageEvent(
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            submission_id=ctx.submission_id,
            run_id=ctx.run_id,
            feature=ctx.feature,
            profile=profile,
            provider=provider,
            model_name=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            input_cost_usd=in_cost,
            output_cost_usd=out_cost,
            total_cost_usd=total,
            token_source=token_source,
            latency_ms=latency_ms,
            is_retry=is_retry
        )
        db.add(event)
        db.commit()
    except Exception as e:
        logger.warning("usage_recorder: dropped a usage event: %s", e)
    finally:
        db.close()

async def record(*, model, provider, profile, prompt_tokens, completion_tokens,
                 latency_ms=None, is_retry=False, token_source="measured"):
    try:
        ctx = get_usage_context()
        await asyncio.to_thread(
            _record_sync, model, provider, profile, prompt_tokens, completion_tokens,
            latency_ms, is_retry, token_source, ctx
        )
    except Exception as e:
        logger.warning("usage_recorder: dropped a usage event: %s", e)
