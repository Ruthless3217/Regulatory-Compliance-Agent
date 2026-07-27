"""Phase 4 (observability): best-effort LLM usage recorder.

The recorder must (a) NEVER raise on a DB failure — losing a metering row must
not break a grade — and (b) stamp the event with cost computed from the current
usage_context + model. The DB insert is stubbed; no Postgres is touched.
"""
import asyncio
from unittest.mock import patch

from app.config import settings
from app.services.observability import usage_recorder
from app.services.observability.usage_context import UsageContext, bind_usage_context


def test_record_never_raises_on_db_error():
    bind_usage_context(UsageContext())
    with patch.object(
        usage_recorder, "_insert_usage_event", side_effect=RuntimeError("db down")
    ):
        # Must complete without propagating the error.
        result = asyncio.run(
            usage_recorder.record(
                model="gpt-test",
                provider="azure",
                profile="main",
                prompt_tokens=10,
                completion_tokens=5,
            )
        )
    assert result is None


def test_record_computes_cost_and_stamps_context(monkeypatch):
    monkeypatch.setattr(
        settings,
        "llm_prices",
        {"gpt-test": {"input": 2.0, "output": 4.0}, "_default": {"input": 0.0, "output": 0.0}},
        raising=False,
    )
    bind_usage_context(
        UsageContext(
            user_id="user-1",
            session_id="sess-1",
            submission_id="sub-1",
            run_id="run-1",
            feature="analysis",
        )
    )
    with patch.object(usage_recorder, "_insert_usage_event") as insert:
        asyncio.run(
            usage_recorder.record(
                model="gpt-test",
                provider="azure",
                profile="main",
                prompt_tokens=1000,
                completion_tokens=500,
                latency_ms=1234,
                is_retry=True,
            )
        )

    assert insert.call_count == 1
    kw = insert.call_args.kwargs
    # attribution copied from the bound context
    assert kw["user_id"] == "user-1"
    assert kw["session_id"] == "sess-1"
    assert kw["submission_id"] == "sub-1"
    assert kw["run_id"] == "run-1"
    assert kw["feature"] == "analysis"
    # metadata carried through
    assert kw["provider"] == "azure"
    assert kw["profile"] == "main"
    assert kw["model"] == "gpt-test"
    assert kw["latency_ms"] == 1234
    assert kw["is_retry"] is True
    assert kw["token_source"] == "measured"
    # cost math: (1000/1000)*2 = 2.0 ; (500/1000)*4 = 2.0
    assert kw["prompt_tokens"] == 1000
    assert kw["completion_tokens"] == 500
    assert kw["total_tokens"] == 1500
    assert kw["input_cost_usd"] == 2.0
    assert kw["output_cost_usd"] == 2.0
    assert kw["total_cost_usd"] == 4.0
    assert kw["price_source"] == "configured"


def test_record_flags_estimated_and_unknown_price(monkeypatch):
    monkeypatch.setattr(
        settings, "llm_prices", {"_default": {"input": 0.0, "output": 0.0}}, raising=False
    )
    bind_usage_context(UsageContext(feature="chat"))
    with patch.object(usage_recorder, "_insert_usage_event") as insert:
        asyncio.run(
            usage_recorder.record(
                model="mystery-model",
                provider="groq",
                profile="chat",
                prompt_tokens=7,
                completion_tokens=0,
                token_source="estimated",
            )
        )
    kw = insert.call_args.kwargs
    assert kw["token_source"] == "estimated"
    assert kw["price_source"] == "default"
    assert kw["feature"] == "chat"
