from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

@dataclass
class UsageContext:
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    submission_id: Optional[str] = None
    run_id: Optional[str] = None
    feature: Optional[str] = None

_usage_context: ContextVar[UsageContext] = ContextVar("usage_context", default=UsageContext())

def get_usage_context() -> UsageContext:
    return _usage_context.get()

def set_usage_context(**kwargs):
    ctx = _usage_context.get()
    new_ctx = UsageContext(
        user_id=kwargs.get("user_id", ctx.user_id),
        session_id=kwargs.get("session_id", ctx.session_id),
        submission_id=kwargs.get("submission_id", ctx.submission_id),
        run_id=kwargs.get("run_id", ctx.run_id),
        feature=kwargs.get("feature", ctx.feature),
    )
    _usage_context.set(new_ctx)

def reset_usage_context():
    _usage_context.set(UsageContext())
