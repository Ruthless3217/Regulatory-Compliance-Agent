"""Langfuse tracing shim — the one place the app touches the Langfuse SDK.

Every traced call site imports ``observe`` / ``trace_root`` / ``update_span``
from here rather than from ``langfuse`` directly, so the pipeline keeps working
(as no-ops) when the SDK is missing or LANGFUSE_* is unset, and so masking,
environment and release are configured exactly once.

Trace shape (see docs/observability-langfuse.md):

    analyze-submission                      chain   (root, engine.py)
    ├── preprocess                          chain   (graph node)
    ├── dispatch                            chain
    │   ├── retrieve-rules                  retriever
    │   ├── retrieve-precedents             retriever
    │   └── retrieve-product-docs           retriever
    ├── analysis                            chain
    │   └── grade-chunk ×N                  chain
    │       ├── llm-structured-response     span    (retry / key-failover wrapper)
    │       │   └── precedent_citation      generation  (langfuse.openai)
    │       └── critic-review-violations    evaluator
    │           └── llm-structured-response → critic_review generation
    ├── disclosure                          chain
    └── scoring                             chain
        └── calculate-scores                tool

``session_id`` is the submission id (all runs + rewrites of one document group
together), ``user_id`` is the reviewer's username.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any, Callable, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

try:
    from langfuse import Langfuse, get_client, observe as _lf_observe, propagate_attributes

    _SDK_AVAILABLE = True
except Exception:  # pragma: no cover - SDK not installed
    Langfuse = None  # type: ignore
    get_client = None  # type: ignore
    _lf_observe = None  # type: ignore
    propagate_attributes = None  # type: ignore
    _SDK_AVAILABLE = False


_client: Any = None
_initialised = False
_enabled = False

# Cap on any single list we summarise into a span (keeps observations readable).
_SUMMARY_LIST_CAP = 50


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

_ID_KEYS = {"id", "uin", "uins", "key_id"}


def _is_identifier_key(key: Any) -> bool:
    k = str(key).lower()
    return k in _ID_KEYS or k.endswith("_id") or k.endswith("_ids") or k.endswith("_uin")


def _mask_value(value: Any) -> Any:
    """``pii.mask_obj`` that leaves identifier-keyed values alone: UUIDs, UINs
    and run ids are join keys back into Postgres, and the digit-run regexes
    (card / Aadhaar) would otherwise rewrite them into ``[CARD]``."""
    from app.services.pii import mask_pii

    if isinstance(value, dict):
        return {
            k: (v if _is_identifier_key(k) and isinstance(v, (str, int)) else _mask_value(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_mask_value(v) for v in value]
    return mask_pii(value)


def _mask(*, data: Any, **_kw: Any) -> Any:
    """SDK mask hook: scrub emails/phones/PAN/Aadhaar/cards from every
    input/output/metadata value before it leaves the process. Langfuse Cloud
    is an external service, so this mirrors what the on-disk LLM log does."""
    try:
        return _mask_value(data)
    except Exception:  # pragma: no cover - masking must never break a trace
        return data


def init_tracing() -> bool:
    """Build the Langfuse client from ``settings`` (idempotent).

    Returns True when tracing is live. Tracing is enabled only when both keys
    are present AND ``LANGFUSE_TRACING_ENABLED`` is not false; otherwise a
    disabled client is registered so ``@observe`` and the OpenAI wrapper are
    cheap no-ops instead of logging auth failures on every call.
    """
    global _client, _initialised, _enabled
    if _initialised:
        return _enabled
    _initialised = True

    if not _SDK_AVAILABLE:
        logger.info("Langfuse SDK not installed; tracing disabled")
        return False

    from app.config import settings

    public = (settings.langfuse_public_key or "").strip()
    secret = (settings.langfuse_secret_key or "").strip()
    wanted = bool(settings.langfuse_tracing_enabled)
    _enabled = wanted and bool(public and secret)

    try:
        kwargs: Dict[str, Any] = {
            "tracing_enabled": _enabled,
            "public_key": public or "disabled",
            "secret_key": secret or "disabled",
            "base_url": settings.langfuse_base_url or None,
            "environment": settings.langfuse_environment or None,
            "release": settings.langfuse_release or None,
            "sample_rate": settings.langfuse_sample_rate,
            "debug": bool(settings.langfuse_debug),
        }
        if settings.langfuse_mask_pii:
            kwargs["mask"] = _mask
        _client = Langfuse(**kwargs)
    except Exception as e:  # pragma: no cover - defensive: never block startup
        logger.warning("Langfuse init failed; tracing disabled: %s", e)
        _enabled = False
        return False

    if _enabled:
        logger.info(
            "Langfuse tracing enabled (host=%s env=%s mask_pii=%s)",
            settings.langfuse_base_url, settings.langfuse_environment or "default",
            settings.langfuse_mask_pii,
        )
    elif wanted:
        logger.info("Langfuse keys not set; tracing disabled")
    return _enabled


def tracing_enabled() -> bool:
    return init_tracing()


def get_langfuse() -> Any:
    """The configured client, or None when tracing is off."""
    return _client if init_tracing() else None


def flush() -> None:
    if _client is not None:
        try:
            _client.flush()
        except Exception as e:  # pragma: no cover
            logger.debug("Langfuse flush failed: %s", e)


def shutdown() -> None:
    if _client is not None:
        try:
            _client.shutdown()
        except Exception as e:  # pragma: no cover
            logger.debug("Langfuse shutdown failed: %s", e)


# ---------------------------------------------------------------------------
# Decorators / context managers
# ---------------------------------------------------------------------------

def observe(
    func: Optional[Callable] = None,
    *,
    name: Optional[str] = None,
    as_type: Optional[str] = None,
    capture_input: Optional[bool] = None,
    capture_output: Optional[bool] = None,
):
    """``langfuse.observe`` with a no-op fallback when the SDK is missing.

    ``as_type`` is one of Langfuse's observation types: span, generation,
    embedding, agent, tool, chain, retriever, evaluator, guardrail.
    """
    if _lf_observe is None:
        def _decorate(fn):
            return fn
        return _decorate(func) if func is not None else _decorate

    kwargs: Dict[str, Any] = {}
    if name is not None:
        kwargs["name"] = name
    if as_type is not None:
        kwargs["as_type"] = as_type
    if capture_input is not None:
        kwargs["capture_input"] = capture_input
    if capture_output is not None:
        kwargs["capture_output"] = capture_output
    if func is not None:
        return _lf_observe(func, **kwargs)
    return _lf_observe(**kwargs)


@contextlib.contextmanager
def trace_attributes(
    *,
    trace_name: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """Apply trace-level attributes to the current observation and every
    child created inside the block (``langfuse.propagate_attributes``).

    Metadata values are stringified: Langfuse only propagates ``dict[str, str]``
    and drops values over 200 chars, so callers pass short identifiers only.
    """
    if propagate_attributes is None or not init_tracing():
        yield
        return
    meta = {k: str(v)[:200] for k, v in (metadata or {}).items() if v is not None} or None
    try:
        cm = propagate_attributes(
            trace_name=trace_name,
            user_id=(str(user_id)[:200] if user_id else None),
            session_id=(str(session_id)[:200] if session_id else None),
            tags=tags or None,
            metadata=meta,
        )
    except Exception as e:  # pragma: no cover - never break the caller
        logger.debug("propagate_attributes failed: %s", e)
        yield
        return
    with cm:
        yield


@contextlib.contextmanager
def trace_root(
    name: str,
    *,
    as_type: str = "span",
    input: Any = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """Open a root observation (a new trace) for code that cannot be decorated,
    e.g. FastAPI route bodies. Yields the observation (or None when disabled)
    so the caller can ``.update(output=...)``.
    """
    client = get_langfuse()
    if client is None:
        yield None
        return
    with client.start_as_current_observation(name=name, as_type=as_type, input=input) as obs:
        with trace_attributes(
            trace_name=name, user_id=user_id, session_id=session_id, tags=tags, metadata=metadata
        ):
            yield obs


def update_span(**kwargs: Any) -> None:
    """``update_current_span`` on the active observation; silent no-op otherwise."""
    client = get_langfuse()
    if client is None:
        return
    try:
        client.update_current_span(**kwargs)
    except Exception as e:  # pragma: no cover
        logger.debug("update_current_span failed: %s", e)


def update_generation(**kwargs: Any) -> None:
    """``update_current_generation`` (model / usage_details / output) on the
    active generation or embedding observation; silent no-op otherwise."""
    client = get_langfuse()
    if client is None:
        return
    try:
        client.update_current_generation(**kwargs)
    except Exception as e:  # pragma: no cover
        logger.debug("update_current_generation failed: %s", e)


def current_trace_id() -> Optional[str]:
    client = get_langfuse()
    if client is None:
        return None
    try:
        return client.get_current_trace_id()
    except Exception:  # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# Summaries — what a reviewer needs at a glance, never the whole graph state
# ---------------------------------------------------------------------------

def summarize_violation(v: Dict[str, Any]) -> Dict[str, Any]:
    """Compact, stable projection of a violation dict for span outputs."""
    meta = v.get("violation_metadata") or {}
    out = {
        "rule_id": v.get("rule_id"),
        "category": v.get("category"),
        "severity": v.get("severity"),
        "confidence": v.get("confidence"),
        "chunk_index": v.get("chunk_index"),
        "description": (v.get("description") or "")[:200],
    }
    if isinstance(meta, dict) and meta.get("grounding"):
        out["grounding"] = meta.get("grounding")
    return out


def summarize_violations(violations: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    items = [v for v in (violations or []) if isinstance(v, dict)]
    by_sev: Dict[str, int] = {}
    for v in items:
        sev = str(v.get("severity") or "unknown").lower()
        by_sev[sev] = by_sev.get(sev, 0) + 1
    out: Dict[str, Any] = {"count": len(items), "by_severity": by_sev}
    out["items"] = [summarize_violation(v) for v in items[:_SUMMARY_LIST_CAP]]
    if len(items) > _SUMMARY_LIST_CAP:
        out["truncated"] = len(items) - _SUMMARY_LIST_CAP
    return out


def summarize_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Node input: counts + identifiers from the LangGraph state."""
    meta = state.get("metadata") or {}
    out: Dict[str, Any] = {
        "submission_id": state.get("submission_id"),
        "status": state.get("status"),
        "chunks": len(state.get("chunks") or []),
        "violations": len(state.get("violations") or []),
        "rule_categories": sorted((state.get("active_rules") or {}).keys()),
    }
    for key in ("declared_product_line", "resolved_product", "resolved_uins", "document_type"):
        if meta.get(key) not in (None, "", [], {}):
            out[key] = meta.get(key)
    return out


def summarize_update(update: Any) -> Any:
    """Node output: the returned partial-state dict, with big collections
    reduced to counts and violations projected compactly."""
    if not isinstance(update, dict):
        return update
    out: Dict[str, Any] = {}
    for key, value in update.items():
        if key == "violations" and isinstance(value, list):
            out[key] = summarize_violations(value)
        elif key == "scores" and isinstance(value, dict):
            out[key] = value
        elif key == "metadata" and isinstance(value, dict):
            out[key] = {
                k: (v if isinstance(v, (str, int, float, bool, type(None))) else f"<{type(v).__name__}:{_size(v)}>")
                for k, v in value.items()
            }
        elif isinstance(value, (list, dict, set, tuple)):
            out[key] = f"<{type(value).__name__}:{len(value)}>"
        elif isinstance(value, (str, int, float, bool, type(None))):
            out[key] = value
        else:
            out[key] = f"<{type(value).__name__}>"
    return out


def _size(v: Any) -> Any:
    try:
        return len(v)
    except Exception:
        return "?"


def graph_node(name: str):
    """Decorate a LangGraph node as a ``chain`` observation whose input/output
    are the summaries above rather than the (huge) raw state."""
    def _decorate(fn):
        import functools

        @functools.wraps(fn)
        async def _wrapped(state, *args, **kwargs):
            update_span(input=summarize_state(state))
            result = await fn(state, *args, **kwargs)
            update_span(output=summarize_update(result))
            return result

        return observe(_wrapped, name=name, as_type="chain", capture_input=False, capture_output=False)

    return _decorate
