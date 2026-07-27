"""Best-effort append-only audit recorder.

Writes one ``audit_events`` row for security (logins), money (runs), and
governance (rule/user changes) events. Call sites: ``auth.py`` (login/logout/
failed), ``rules.py`` (rule_* with before/after), ``admin_console.py`` (user_*),
the run tracker (analysis_*), and the feedback route.

Reliability contract: like the usage recorder this is **best-effort** — it never
raises. The synchronous DB write runs in a worker thread via ``asyncio.to_thread``
(the app uses synchronous SQLAlchemy).

Note: ``AuditEvent`` maps its JSONB blob to the python attribute
``event_metadata`` (the DB column is ``"metadata"``, which SQLAlchemy reserves on
the declarative base). The public API here takes ``metadata=`` and translates.
"""
from __future__ import annotations

import logging
import asyncio
from typing import Any, Optional

from app.api.rate_limit import extract_client_key
from app.config import settings

logger = logging.getLogger(__name__)


async def record(
    event_type: str,
    *,
    actor: Any = None,
    request: Any = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    before: Any = None,
    after: Any = None,
    metadata: Any = None,
) -> None:
    """Record one audit event. Never raises.

    ``actor_ip`` is derived from ``request`` via the same ``extract_client_key``
    used by the rate limiter (respects ``trust_forwarded_for``); ``session_id``
    comes from ``request.state.session_id`` (set by ``get_current_user``).
    """
    try:
        actor_ip = None
        if request is not None:
            actor_ip = extract_client_key(
                request, trust_forwarded_for=settings.trust_forwarded_for
            )
        session_id = getattr(getattr(request, "state", None), "session_id", None)
        actor_user_id = str(actor.id) if actor is not None else None
        actor_role = getattr(actor, "role", None)

        await asyncio.to_thread(
            _insert_audit_event,
            event_type=event_type,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            actor_ip=actor_ip,
            session_id=session_id,
            target_type=target_type,
            target_id=target_id,
            before=before,
            after=after,
            metadata=metadata,
        )
    except Exception as e:  # noqa: BLE001 - audit must not break the request
        logger.warning("audit: dropped event %s: %s", event_type, e)


def _insert_audit_event(*, metadata: Any = None, **fields) -> None:
    """Synchronous insert of one ``AuditEvent`` in its own short-lived session.

    Translates the public ``metadata=`` kwarg to the model's ``event_metadata``
    attribute (DB column ``"metadata"``).
    """
    from app.database import SessionLocal
    from app.models.audit_event import AuditEvent

    db = SessionLocal()
    try:
        db.add(AuditEvent(event_metadata=metadata, **fields))
        db.commit()
    finally:
        db.close()
