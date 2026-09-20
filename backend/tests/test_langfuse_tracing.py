"""Langfuse tracing shim + instrumentation — fully offline.

Two layers:

1. The shim (``app.services.observability.tracing``): no-ops without keys,
   PII masking, compact state summaries, the ``graph_node`` wrapper.
2. The wiring: a real ``LLMService`` call driven through an httpx
   ``MockTransport`` (no provider quota spent) with the Langfuse SDK exporting
   to an in-memory OTel exporter, asserting the trace *shape* the docs promise:
   root chain → wrapper span → purpose-named generation with model + usage.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from app.services.observability import tracing


# ---------------------------------------------------------------- helpers

@pytest.fixture
def fresh_tracing(monkeypatch):
    """Reset the shim's module-level singleton between tests."""
    monkeypatch.setattr(tracing, "_initialised", False)
    monkeypatch.setattr(tracing, "_client", None)
    monkeypatch.setattr(tracing, "_enabled", False)
    yield
    monkeypatch.setattr(tracing, "_initialised", False)
    monkeypatch.setattr(tracing, "_client", None)
    monkeypatch.setattr(tracing, "_enabled", False)


def _settings(monkeypatch, **overrides):
    from app.config import settings
    base = dict(
        langfuse_public_key="", langfuse_secret_key="",
        langfuse_base_url="https://cloud.langfuse.com", langfuse_tracing_enabled=True,
        langfuse_environment="test", langfuse_release="", langfuse_sample_rate=None,
        langfuse_mask_pii=True, langfuse_debug=False,
    )
    base.update(overrides)
    for k, v in base.items():
        monkeypatch.setattr(settings, k, v)


# ------------------------------------------------------------ shim: off

def test_disabled_without_keys_everything_is_a_noop(fresh_tracing, monkeypatch):
    _settings(monkeypatch)
    assert tracing.init_tracing() is False
    assert tracing.get_langfuse() is None

    @tracing.observe(name="x", as_type="chain")
    async def fn(a):
        tracing.update_span(input={"a": a})
        return a * 2

    assert asyncio.run(fn(2)) == 4
    with tracing.trace_attributes(trace_name="t", user_id="u"):
        pass
    with tracing.trace_root("r") as obs:
        assert obs is None
    assert tracing.current_trace_id() is None


def test_disabled_by_flag_even_with_keys(fresh_tracing, monkeypatch):
    _settings(monkeypatch, langfuse_public_key="pk", langfuse_secret_key="sk",
              langfuse_tracing_enabled=False)
    assert tracing.init_tracing() is False


def test_enabled_with_keys_builds_client_with_mask_and_environment(fresh_tracing, monkeypatch):
    captured = {}

    class FakeLangfuse:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(tracing, "Langfuse", FakeLangfuse)
    _settings(monkeypatch, langfuse_public_key="pk-lf-x", langfuse_secret_key="sk-lf-y",
              langfuse_environment="local", langfuse_release="1.2.3")
    assert tracing.init_tracing() is True
    assert tracing.init_tracing() is True  # idempotent
    assert captured["public_key"] == "pk-lf-x"
    assert captured["secret_key"] == "sk-lf-y"
    assert captured["tracing_enabled"] is True
    assert captured["environment"] == "local"
    assert captured["release"] == "1.2.3"
    assert captured["mask"] is tracing._mask


def test_mask_pii_off_omits_mask(fresh_tracing, monkeypatch):
    captured = {}

    class FakeLangfuse:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(tracing, "Langfuse", FakeLangfuse)
    _settings(monkeypatch, langfuse_public_key="pk", langfuse_secret_key="sk", langfuse_mask_pii=False)
    tracing.init_tracing()
    assert "mask" not in captured


# --------------------------------------------------------------- masking

def test_mask_scrubs_pii_recursively():
    data = {"prompt": "Call 9876543210 or mail a.b@bajajlife.com PAN ABCDE1234F",
            "nested": [{"t": "Aadhaar 1234 5678 9012"}], "n": 3}
    out = tracing._mask(data=data)
    assert "[PHONE]" in out["prompt"] and "[EMAIL]" in out["prompt"] and "[PAN]" in out["prompt"]
    assert out["nested"][0]["t"] == "Aadhaar [AADHAAR]"
    assert out["n"] == 3


def test_mask_leaves_identifier_keys_intact():
    """UUIDs / UINs / run ids are join keys, not PII — a digit-heavy id must
    not be rewritten into [CARD]/[AADHAAR] or the trace stops being searchable."""
    data = {
        "rule_id": "00000000-0000-0000-0000-000000000001",
        "run_id": "12345678-1234-1234-1234-123456789012",
        "id": "1234 5678 9012",
        "uin": "116L205V01",
        "items": [{"violation_id": "0000-0000-0000-1234567890123", "description": "call 9876543210"}],
        "text": "1234 5678 9012",
    }
    out = tracing._mask(data=data)
    assert out["rule_id"] == data["rule_id"]
    assert out["run_id"] == data["run_id"]
    assert out["id"] == data["id"]
    assert out["uin"] == data["uin"]
    assert out["items"][0]["violation_id"] == data["items"][0]["violation_id"]
    assert out["items"][0]["description"] == "call [PHONE]"
    assert out["text"] == "[AADHAAR]"


# ------------------------------------------------------------- summaries

def test_summarize_state_is_counts_not_content():
    state = {
        "submission_id": "s1", "status": "running",
        "chunks": [{"text": "x" * 5000}] * 3,
        "violations": [{"severity": "high"}],
        "active_rules": {"claims": [1, 2], "tax": [3]},
        "metadata": {"declared_product_line": "ULIP", "resolved_product": "Goal Assure"},
    }
    out = tracing.summarize_state(state)
    assert out == {
        "submission_id": "s1", "status": "running", "chunks": 3, "violations": 1,
        "rule_categories": ["claims", "tax"],
        "declared_product_line": "ULIP", "resolved_product": "Goal Assure",
    }
    assert "x" * 100 not in json.dumps(out)


def test_summarize_update_projects_violations_and_collapses_collections():
    v = {"rule_id": "r1", "category": "claims", "severity": "Critical", "confidence": 0.9,
         "chunk_index": 2, "description": "d" * 500, "violation_metadata": {"grounding": "precedent"},
         "current_text": "SECRET PASSAGE"}
    out = tracing.summarize_update({
        "violations": [v] * 60, "scores": {"overall": 71}, "chunks": [1, 2],
        "status": "done", "metadata": {"chunk_keys": {"a": 1}, "flag": True},
    })
    assert out["violations"]["count"] == 60
    assert out["violations"]["by_severity"] == {"critical": 60}
    assert len(out["violations"]["items"]) == 50 and out["violations"]["truncated"] == 10
    item = out["violations"]["items"][0]
    assert item["grounding"] == "precedent" and len(item["description"]) == 200
    assert "current_text" not in item
    assert out["scores"] == {"overall": 71}
    assert out["chunks"] == "<list:2>"
    assert out["status"] == "done"
    assert out["metadata"] == {"chunk_keys": "<dict:1>", "flag": True}
    assert tracing.summarize_update(None) is None


def test_graph_node_preserves_result_and_name(fresh_tracing, monkeypatch):
    _settings(monkeypatch)
    tracing.init_tracing()

    @tracing.graph_node("preprocess")
    async def preprocess_node(state):
        return {"chunks": [1, 2, 3]}

    assert preprocess_node.__name__ == "preprocess_node"
    assert asyncio.run(preprocess_node({"submission_id": "s"})) == {"chunks": [1, 2, 3]}


# ------------------------------------------------- LLMService trace kwargs

def test_trace_kwargs_only_when_langfuse_openai_is_active(monkeypatch):
    from app.services import llm_service as mod
    svc = mod.LLMService("main")
    monkeypatch.setattr(mod, "_LANGFUSE_OPENAI", True)
    kw = svc._trace_kwargs("precedent_citation", attempt=0, key_id=None)
    assert kw["name"] == "precedent_citation"
    assert kw["metadata"]["profile"] == "main"
    assert kw["metadata"]["attempt"] == 0 and "key_id" not in kw["metadata"]
    monkeypatch.setattr(mod, "_LANGFUSE_OPENAI", False)
    assert svc._trace_kwargs("precedent_citation") == {}


# ------------------------------------------- end-to-end shape, in memory

class _Out(BaseModel):
    verdict: str
    score: int


def _completion(content: str, *, prompt_tokens=42, completion_tokens=7):
    return {
        "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "gpt-test",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens},
    }


@pytest.fixture
def inmemory_langfuse(fresh_tracing, monkeypatch):
    """A real Langfuse client whose spans land in an in-memory OTel exporter."""
    langfuse = pytest.importorskip("langfuse")
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from langfuse._client.resource_manager import LangfuseResourceManager

    # conftest forces the SDK off via env; this client exports in-memory only,
    # so it is safe to switch on here.
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    LangfuseResourceManager.reset()
    exporter = InMemorySpanExporter()
    client = langfuse.Langfuse(
        public_key="pk-lf-test", secret_key="sk-lf-test", base_url="http://langfuse.invalid",
        tracing_enabled=True, mask=tracing._mask, span_exporter=exporter, environment="test",
    )
    monkeypatch.setattr(tracing, "_initialised", True)
    monkeypatch.setattr(tracing, "_client", client)
    monkeypatch.setattr(tracing, "_enabled", True)
    yield client, exporter
    client.flush()
    LangfuseResourceManager.reset()


def _spans(exporter):
    return {s.name: s for s in exporter.get_finished_spans()}


def test_structured_call_produces_named_generation_under_wrapper_span(inmemory_langfuse, monkeypatch):
    from app.services import llm_service as mod
    if not mod._LANGFUSE_OPENAI:
        pytest.skip("langfuse.openai wrapper not active")
    client, exporter = inmemory_langfuse

    # No Redis / DB side-effects during the call.
    from app.services import llm_budget
    stub = SimpleNamespace(reserve=_noop, reconcile=_noop)
    monkeypatch.setattr(llm_budget, "get_global_budget", lambda: stub)
    monkeypatch.setattr(mod.usage_recorder, "record", _noop)

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_completion(json.dumps({"verdict": "ok", "score": 9})))

    svc = mod.LLMService("main")
    svc.provider = "openai"
    svc.model = "gpt-test"
    fake = mod.AsyncOpenAI(
        api_key="test", base_url="http://llm.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    svc._client_pool = [("testkey", fake)]

    @tracing.observe(name="analyze-submission", as_type="chain", capture_input=False)
    async def root():
        with tracing.trace_attributes(trace_name="analyze-submission", user_id="reviewer1",
                                      session_id="sub-1", tags=["compliance-analysis"],
                                      metadata={"submission_id": "sub-1"}):
            return await svc.generate_structured_response(
                prompt="Grade this: contact 9876543210", output_model=_Out,
                system_prompt="sys", tool_name="precedent_citation", temperature=0.0,
            )

    result = asyncio.run(root())
    assert result == _Out(verdict="ok", score=9)
    # The Langfuse-only kwargs never reached the provider.
    assert "name" not in seen["body"] and "metadata" not in seen["body"]

    client.flush()
    spans = _spans(exporter)
    assert {"analyze-submission", "llm-structured-response", "precedent_citation"} <= set(spans)

    root_s, wrap_s, gen_s = spans["analyze-submission"], spans["llm-structured-response"], spans["precedent_citation"]
    # Hierarchy: root → wrapper span → generation, all in one trace.
    assert wrap_s.parent.span_id == root_s.context.span_id
    assert gen_s.parent.span_id == wrap_s.context.span_id
    assert len({s.context.trace_id for s in spans.values()}) == 1

    a = gen_s.attributes
    assert a["langfuse.observation.type"] == "generation"
    assert a["langfuse.observation.model.name"] == "gpt-test"
    usage = json.loads(a["langfuse.observation.usage_details"])
    assert usage["prompt_tokens"] == 42 and usage["completion_tokens"] == 7
    # Prompt is on the generation, PII-masked before export.
    gen_input = a["langfuse.observation.input"]
    assert "Grade this" in gen_input and "9876543210" not in gen_input and "[PHONE]" in gen_input
    assert a["langfuse.observation.metadata.profile"] == "main"
    assert a["langfuse.observation.metadata.attempt"] == 0

    # Wrapper span: purpose/schema in, parsed result out.
    w = wrap_s.attributes
    assert w["langfuse.observation.type"] == "span"
    assert json.loads(w["langfuse.observation.input"])["output_schema"] == "_Out"
    assert json.loads(w["langfuse.observation.output"]) == {"verdict": "ok", "score": 9}

    # Trace attributes propagated to every observation.
    for s in spans.values():
        assert s.attributes["user.id"] == "reviewer1"
        assert s.attributes["session.id"] == "sub-1"
        assert s.attributes["langfuse.trace.name"] == "analyze-submission"
        assert "compliance-analysis" in s.attributes["langfuse.trace.tags"]


async def _noop(*_a, **_kw):
    return None
