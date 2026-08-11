"""Re-running the same analysis must produce the same inputs and the same order.

Every case here is a confirmed source of run-to-run drift, not a hypothetical:
  - the prompt fence was a fresh uuid4 per call, so two runs over identical
    content produced byte-different prompts (and never hit prompt caching);
  - the LangGraph thread_id was the submission id, and `violations` is an
    `operator.add` reducer channel — a re-run RESUMED the finished thread and
    appended a second copy of every finding (3 -> 6 -> 9);
  - the retrieval legs, the active-rule query and the disclosure trigger union
    all had unordered output feeding a hard top-N cut.
"""
import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.services.agents.compliance.engine import ComplianceEngine
from app.services.disclaimer.registry import Disclaimer
from app.services.disclaimer.triggers import ProductContext, resolve_required
from app.services.preprocessing_service import ContextEngineeringService, _content_fence
from app.services.rag.rrf import reciprocal_rank_fusion
from app.services.rule_generator_service import RuleGeneratorService


CONTENT = "Guaranteed 15% returns. Best plan in India. Tax free under Section 80C."
RULES = {
    "irdai": [
        {"id": "11111111-1111-1111-1111-111111111111",
         "rule_text": "No guaranteed-return claims", "severity": "critical",
         "source_quote": "shall not guarantee returns"},
    ]
}


# --------------------------------------------------------------- fences ---

def test_fence_is_stable_for_identical_content():
    assert _content_fence(CONTENT) == _content_fence(CONTENT)
    assert _content_fence(CONTENT).startswith("UNTRUSTED-")


def test_fence_changes_with_the_fenced_content():
    assert _content_fence(CONTENT) != _content_fence(CONTENT + " ")
    # Every fenced block contributes: a different precedent block must not be
    # able to reuse the fence a document alone would have produced.
    assert _content_fence(CONTENT, "ctx-a") != _content_fence(CONTENT, "ctx-b")
    # ...and the parts are separated, so ("ab", "") != ("a", "b").
    assert _content_fence("ab", "") != _content_fence("a", "b")


def test_rule_prompt_is_byte_identical_across_runs():
    svc = ContextEngineeringService(db=None)
    first = svc.create_compliance_prompts(CONTENT, RULES)
    second = svc.create_compliance_prompts(CONTENT, RULES)

    assert first == second
    assert "UNTRUSTED-" in first
    assert svc.create_compliance_prompts(CONTENT + "!", RULES) != first


def test_precedent_prompt_is_byte_identical_across_runs():
    svc = ContextEngineeringService(db=None)
    precedents = [{"highlighted_span": "Guaranteed 15% returns",
                   "reviewer_comment": "Remove the guarantee.", "severity": "critical"}]
    kwargs = dict(precedents=precedents, rules=[], document_context="Full document text.")

    first = svc.create_precedent_prompts(CONTENT, **kwargs)
    assert first == svc.create_precedent_prompts(CONTENT, **kwargs)
    assert first != svc.create_precedent_prompts(CONTENT, **{**kwargs,
                                                             "document_context": "Other."})


# ------------------------------------------------------------------ RRF ---

def test_rrf_fusion_is_deterministic_for_fixed_legs():
    vector_leg = [("b", 0.91), ("a", 0.90), ("c", 0.42)]
    keyword_leg = [("c", 3.0), ("a", 2.5), ("d", 0.1)]

    first = reciprocal_rank_fusion([vector_leg, keyword_leg], k=60)
    second = reciprocal_rank_fusion([vector_leg, keyword_leg], k=60)

    assert first == second
    # Ties (b and d are both rank-0-only... here rank 0 vs rank 2) must not
    # reshuffle: equal fused scores keep first-seen order via a stable sort.
    tied = reciprocal_rank_fusion([[("x", 1.0)], [("y", 1.0)]], k=60)
    assert tied == reciprocal_rank_fusion([[("x", 1.0)], [("y", 1.0)]], k=60)
    assert [doc_id for doc_id, _ in tied] == ["x", "y"]


def test_retrieval_legs_order_by_a_stable_tiebreak():
    # Both legs cut at :recall, so rows with an identical score must not be able
    # to swap places between runs and change what reaches fusion.
    from app.services.rag.stores.pgvector_store import _keyword_leg_sql, _vector_leg_sql

    assert "ORDER BY score DESC, id" in _keyword_leg_sql("precedents", "")
    assert "ORDER BY embedding <=> CAST(:qvec AS VECTOR), id" in _vector_leg_sql("rules", "")


# ---------------------------------------------------------- active rules ---

class _RecordingQuery:
    """Applies whatever ordering the service asks for, and remembers it."""

    def __init__(self, rows):
        self._rows = list(rows)
        self.order_by_keys = None

    def filter(self, *_a, **_kw):
        return self

    def order_by(self, *cols):
        self.order_by_keys = [c.key for c in cols]
        self._rows.sort(key=lambda r: tuple(str(getattr(r, k)) for k in self.order_by_keys))
        return self

    def all(self):
        return list(self._rows)


def test_get_active_rules_is_ordered_by_category_then_id():
    rows = [
        SimpleNamespace(id="c", category="irdai"),
        SimpleNamespace(id="a", category="sebi"),
        SimpleNamespace(id="b", category="irdai"),
        SimpleNamespace(id="a", category="irdai"),
    ]
    query = _RecordingQuery(rows)
    db = SimpleNamespace(query=lambda *_a, **_kw: query)

    grouped = RuleGeneratorService().get_active_rules(db)

    assert query.order_by_keys == ["category", "id"]
    # Equal categories fall back to id, and grouping preserves query order —
    # the per-chunk `[:8]` cut therefore keeps the same rules every run.
    assert [r.id for r in grouped["irdai"]] == ["a", "b", "c"]
    assert [r.id for r in grouped["sebi"]] == ["a"]


# ------------------------------------------------------ disclosure order ---

def _disclaimer(did, obligation_type):
    return Disclaimer(
        id=did, type="tax", text=f"{did} text", anchors=[], severity="high",
        altered_severity="moderate",
        triggers={"llm_obligation_type": obligation_type},
        present_threshold=0.9, altered_threshold=0.6, precedence=1, source="test",
    )


class _FakeRegistry:
    def __init__(self, disclaimers):
        self._by_id = {d.id: d for d in disclaimers}

    def all(self):
        return list(self._by_id.values())

    def get(self, did):
        return self._by_id.get(did)


def test_llm_obligations_are_emitted_in_sorted_order():
    registry = _FakeRegistry([
        _disclaimer("zeta_disclaimer", "zeta"),
        _disclaimer("alpha_disclaimer", "alpha"),
        _disclaimer("mid_disclaimer", "mid"),
    ])
    ctx = ProductContext(is_ulip=False, is_par=False, has_product=True)

    async def llm_call(_text, _types):
        return ["zeta", "alpha", "mid"]

    required, degraded = asyncio.run(
        resolve_required("doc", ctx, registry, llm_call=llm_call)
    )

    assert degraded is False
    assert list(required) == sorted(required)
    assert list(required) == ["alpha_disclaimer", "mid_disclaimer", "zeta_disclaimer"]


def test_partially_swept_disclosure_run_is_not_gradeable():
    # A partial sweep means some required disclaimers were never checked, so the
    # findings list is knowingly incomplete — review it, never grade it.
    state = {
        "chunks": [{"id": "chunk-1", "text": "creative"}],
        "status": "completed",
        "metadata": {"disclosure_recall_degraded": True},
    }

    assert ComplianceEngine.evaluate_persistability(state) == (
        False, "disclosure_recall_degraded",
    )
    assert "disclosure_recall_degraded" in ComplianceEngine._NEEDS_REVIEW_REASONS


# ------------------------------------------------------------ thread ids ---

class _SubmissionQuery:
    def __init__(self, submission):
        self._submission = submission

    def filter(self, *_a, **_kw):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self._submission


class _FakeDB:
    def __init__(self, submission):
        self._submission = submission

    def query(self, *_a, **_kw):
        return _SubmissionQuery(self._submission)

    def add(self, *_a):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass


@pytest.fixture()
def captured_graph_configs(monkeypatch):
    """Run analyze_submission with the graph stubbed out; collect its configs."""
    from app.services.agents import orchestrator as orchestrator_module
    from app.services import run_tracker

    configs = []

    async def fake_run_workflow(_state, config=None):
        configs.append(config)
        # Empty chunks => not persistable, so the run stops right after the
        # graph call; the thread id is all this test cares about.
        return {"chunks": [], "status": "completed", "metadata": {}}

    async def fake_open_run(_db, _submission_id, _user, _session_id, **_kw):
        return SimpleNamespace(id=uuid.uuid4())

    async def fake_close_run(*_a, **_kw):
        return None

    monkeypatch.setattr(orchestrator_module.orchestrator, "run_workflow", fake_run_workflow)
    monkeypatch.setattr(run_tracker, "open_run", fake_open_run)
    monkeypatch.setattr(run_tracker, "close_run", fake_close_run)
    return configs


def test_each_run_gets_its_own_graph_thread(captured_graph_configs):
    submission_id = str(uuid.uuid4())
    submission = SimpleNamespace(
        id=submission_id, status="pending", submitted_by=None,
        product_line="term", title="Creative",
    )
    db = _FakeDB(submission)

    assert asyncio.run(ComplianceEngine.analyze_submission(submission_id, db)) is None
    assert asyncio.run(ComplianceEngine.analyze_submission(submission_id, db)) is None

    threads = [c["configurable"]["thread_id"] for c in captured_graph_configs]
    assert len(threads) == 2
    # Distinct per run: sharing a thread resumed the finished checkpoint and
    # re-added every violation through the operator.add reducer.
    assert threads[0] != threads[1]
    # Still traceable back to the submission it belongs to.
    assert all(t.startswith(f"{submission_id}:") for t in threads)
