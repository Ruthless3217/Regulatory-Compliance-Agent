import json
import logging
from app.api.rate_limit import extract_client_key
from app.config import settings

logger = logging.getLogger(__name__)


def _json_safe(v):
    """Coerce a payload into something the JSONB column can store. Decimal / UUID
    / datetime (etc.) aren't JSON-serializable by default and would otherwise
    make the whole audit event drop — round-trip through json with default=str."""
    if v is None:
        return None
    try:
        return json.loads(json.dumps(v, default=str))
    except Exception:
        return {"_unserializable": str(v)}


async def record(event_type, *, actor=None, request=None, target_type=None, target_id=None,
                 before=None, after=None, metadata=None, scope_submission_id=None):
    """Best-effort audit write on its own session. Failures are swallowed.

    Right for telemetry (logins, denials, exports), wrong for anything the
    reviewer trail has to be able to defend — use `record_sync` for those.
    """
    try:
        from app.database import SessionLocal
        from app.models.audit_event import AuditEvent

        ip = extract_client_key(request, trust_forwarded_for=settings.trust_forwarded_for) if request else None
        session_id = getattr(getattr(request, 'state', None), 'session_id', None)

        with SessionLocal() as db:
            event = AuditEvent(
                event_type=event_type,
                actor_user_id=str(actor.id) if actor else None,
                actor_role=getattr(actor, "role", None),
                actor_ip=ip,
                session_id=session_id,
                scope_submission_id=scope_submission_id,
                target_type=target_type,
                target_id=target_id,
                before_state=_json_safe(before),
                after_state=_json_safe(after),
                metadata_=_json_safe(metadata)
            )
            db.add(event)
            db.commit()
    except Exception as e:
        logger.warning("audit: dropped event %s: %s", event_type, e)


def record_sync(db, event_type, *, actor=None, request=None, target_type=None,
                target_id=None, before=None, after=None, metadata=None,
                scope_submission_id=None):
    """Write an audit event into the CALLER's session, without committing.

    The async `record` above is best-effort: its own session, its own commit,
    every exception swallowed. For telemetry that is the right trade. For the
    reviewer trail it is not — a mutation could commit while its audit row
    silently vanished, and an absent row is indistinguishable from an action
    that never happened.

    This adds the event to the caller's session so the caller's existing commit
    carries it: the event and the change it describes commit together or not at
    all. Exceptions are deliberately NOT caught — a failure here should roll the
    caller back rather than produce an unrecorded change.
    """
    from app.models.audit_event import AuditEvent

    ip = (
        extract_client_key(request, trust_forwarded_for=settings.trust_forwarded_for)
        if request else None
    )
    session_id = getattr(getattr(request, "state", None), "session_id", None)

    event = AuditEvent(
        event_type=event_type,
        actor_user_id=str(actor.id) if actor is not None else None,
        actor_role=getattr(actor, "role", None),
        actor_ip=ip,
        session_id=session_id,
        scope_submission_id=scope_submission_id,
        target_type=target_type,
        target_id=target_id,
        before_state=_json_safe(before),
        after_state=_json_safe(after),
        metadata_=_json_safe(metadata),
    )
    db.add(event)
    return event
