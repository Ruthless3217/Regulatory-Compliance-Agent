"""Per-candidate retrieval trace (migration 0037, app/services/rag/trace.py).

Three things must hold or the inspector lies:
  1. Collection is OFF by default and costs nothing — every retrieval path that
     is not an analysis run must be untouched.
  2. What is captured is what the store saw: leg membership decides
     retrieval_method, and the ranks are the ranks.
  3. The final_status vocabulary is derived, never guessed — in particular
     dropped_by_cap, which has no other record anywhere.
And persistence is fail-soft: observability may not take an analysis down.
"""
from types import SimpleNamespace

import pytest

from app.services.agents.graph import nodes
from app.services.rag import trace as rag_trace
from app.services.rag.ports import SearchHit
from app.services.rag.retrievers.precedent_retriever import _hit_to_precedent


# --- collector is opt-in ----------------------------------------------------

def test_no_collector_means_no_tracer_and_no_work():
    assert rag_trace.begin_query(index="rag_rules", top_k=8, filters=None) is None


def test_query_labels_without_a_collector_are_still_a_no_op():
    with rag_trace.query(chunk_id="c1", category="regulatory"):
        assert rag_trace.begin_query(index="rag_rules", top_k=8, filters=None) is None


def test_stop_returns_what_was_collected_and_unsets_the_collector():
    token = rag_trace.start()
    tracer = rag_trace.begin_query(index="rag_rules", top_k=2, filters={"is_active": True})
    tracer.record([("a", 0.9)], [], [("a", 0.016)])
    rows = rag_trace.stop(token)

    assert [r.candidate_id for r in rows] == ["a"]
    assert rag_trace.begin_query(index="rag_rules", top_k=2, filters=None) is None


# --- capture correctness ----------------------------------------------------

def _capture(vec, kw, fused, *, top_k=2, chunk_id="c1", category=None, index="rag_rules"):
    token = rag_trace.start()
    try:
        with rag_trace.query(chunk_id=chunk_id, category=category):
            tracer = rag_trace.begin_query(index=index, top_k=top_k, filters={"is_active": True})
        tracer.record(vec, kw, fused)
        return rag_trace.stop(token)
    except BaseException:
        rag_trace.stop(token)  # never leak a collector into the next test
        raise


def test_retrieval_method_is_leg_set_membership():
    rows = _capture(
        vec=[("both", 0.81), ("vec_only", 0.44)],
        kw=[("both", 0.30), ("kw_only", 0.11)],
        fused=[("both", 0.032), ("vec_only", 0.016), ("kw_only", 0.015)],
        top_k=3,
    )
    method = {r.candidate_id: r.retrieval_method for r in rows}
    assert method == {"both": "both", "vec_only": "vector", "kw_only": "bm25"}


def test_each_leg_keeps_its_own_score_and_1_based_rank():
    rows = _capture(
        vec=[("a", 0.81), ("b", 0.44)],
        kw=[("b", 0.30), ("a", 0.11)],
        fused=[("a", 0.032), ("b", 0.031)],
        top_k=2,
    )
    by_id = {r.candidate_id: r for r in rows}
    assert (by_id["a"].cosine, by_id["a"].vector_rank) == (0.81, 1)
    assert (by_id["a"].ts_rank, by_id["a"].bm25_rank) == (0.11, 2)
    assert (by_id["b"].cosine, by_id["b"].vector_rank) == (0.44, 2)
    assert (by_id["b"].ts_rank, by_id["b"].bm25_rank) == (0.30, 1)
    assert [r.fused_rank for r in rows] == [1, 2]


def test_a_candidate_only_in_one_leg_has_no_score_for_the_other():
    rows = _capture(vec=[("a", 0.7)], kw=[], fused=[("a", 0.016)])
    assert rows[0].ts_rank is None and rows[0].bm25_rank is None
    assert rows[0].cosine == 0.7


def test_the_query_label_attributes_rows_to_the_chunk_that_asked():
    rows = _capture(vec=[("a", 0.7)], kw=[], fused=[("a", 0.016)],
                    chunk_id="chunk-9", category="regulatory")
    assert (rows[0].chunk_id, rows[0].category) == ("chunk-9", "regulatory")
    assert rows[0].corpus == "rules"          # index name -> applicability vocabulary
    assert rows[0].filters == {"index": "rag_rules", "applied": {"is_active": True}}


def test_precedent_indexes_both_map_onto_the_precedents_corpus():
    for index in ("precedent_cases", "rag_compliance_examples"):
        rows = _capture(vec=[("a", 0.7)], kw=[], fused=[("a", 0.016)], index=index)
        assert rows[0].corpus == "precedents"


def test_the_near_miss_band_is_bounded_per_query_not_truncated_per_run():
    fused = [(f"id{i}", 1.0 / (i + 1)) for i in range(200)]
    rows = _capture(vec=[(d, 0.5) for d, _ in fused], kw=[], fused=fused, top_k=8)
    assert len(rows) == 8 + rag_trace.NEAR_MISS
    assert rows[-1].fused_rank == 8 + rag_trace.NEAR_MISS


def test_a_recording_failure_never_propagates_into_retrieval():
    token = rag_trace.start()
    tracer = rag_trace.begin_query(index="rag_rules", top_k=2, filters=None)
    tracer.record(None, None, None)  # would raise if it were not swallowed
    assert rag_trace.stop(token) == []


# --- the store actually feeds the collector ---------------------------------

class _StoreDb:
    """Just enough Session for PgVectorStore.hybrid_search._do()."""

    def __init__(self, vec, kw, ids):
        self.vec, self.kw, self.ids = vec, kw, ids

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "embedding <=>" in sql:
            return _Rows(self.vec)
        if "ts_rank_cd" in sql:
            return _Rows(self.kw)
        return _Rows([(i, "cat", "sev", True, "text", [], "src") for i in self.ids])

    def close(self):
        pass


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_the_store_records_through_run_in_executor(monkeypatch):
    """The load-bearing plumbing assumption: loop.run_in_executor does NOT copy
    the context, so the tracer must be resolved in hybrid_search's async body
    and carried into the worker thread. If this ever regresses, production
    traces silently come back empty while every unit test still passes."""
    import uuid as _uuid

    from app.services.rag.stores import pgvector_store as ps

    a, b = str(_uuid.uuid4()), str(_uuid.uuid4())
    db = _StoreDb(vec=[(a, 0.9), (b, 0.4)], kw=[(b, 0.3)], ids=[a, b])
    monkeypatch.setattr(ps.PgVectorStore, "_session", lambda self: db)
    monkeypatch.setattr(ps, "_assert_embedding_compat", lambda *_a: None)

    token = rag_trace.start()
    try:
        with rag_trace.query(chunk_id="c7", category="regulatory"):
            hits = await ps.PgVectorStore().hybrid_search(
                index="rag_rules", query_text="guaranteed returns",
                query_vector=[0.1, 0.2], top_k=2, recall_pool=30, rrf_k=60,
                filters={"is_active": True},
            )
    finally:
        rows = rag_trace.stop(token)

    assert [h.id for h in hits] == [a, b] or [h.id for h in hits] == [b, a]
    by_id = {r.candidate_id: r for r in rows}
    assert by_id[a].retrieval_method == "vector" and by_id[a].cosine == 0.9
    assert by_id[b].retrieval_method == "both" and by_id[b].ts_rank == 0.3
    assert by_id[a].chunk_id == "c7" and by_id[a].category == "regulatory"
    assert by_id[a].fused_rank == 1 or by_id[b].fused_rank == 1


@pytest.mark.asyncio
async def test_the_store_is_untouched_when_nothing_is_collecting(monkeypatch):
    import uuid as _uuid

    from app.services.rag.stores import pgvector_store as ps

    a = str(_uuid.uuid4())
    db = _StoreDb(vec=[(a, 0.9)], kw=[], ids=[a])
    monkeypatch.setattr(ps.PgVectorStore, "_session", lambda self: db)
    monkeypatch.setattr(ps, "_assert_embedding_compat", lambda *_a: None)

    hits = await ps.PgVectorStore().hybrid_search(
        index="rag_rules", query_text="x", query_vector=[0.1], top_k=2,
        recall_pool=30, rrf_k=60, filters=None,
    )
    assert [h.id for h in hits] == [a]


# --- final_status vocabulary ------------------------------------------------

RUN = "11111111-1111-1111-1111-111111111111"


def _traced(cid, **kw):
    base = dict(
        corpus="rules", index="rag_rules", chunk_id="c1", category="regulatory",
        candidate_id=cid, retrieval_method="both", cosine=0.8, vector_rank=1,
        ts_rank=0.2, bm25_rank=1, fused_score=0.032, fused_rank=1, top_k=8,
        filters={"index": "rag_rules", "applied": None},
    )
    base.update(kw)
    return rag_trace.TracedCandidate(**base)


def _gated(cid, **kw):
    base = dict(
        corpus="rules", id=cid, score=0.032, scope_value="term",
        verdict="accepted", reason="category_match: term",
        tier="chunk_rules", chunk_id="c1", category="regulatory",
    )
    base.update(kw)
    return base


def _status(rows):
    return {r["candidate_id"]: (r["final_status"], r["deciding_stage"]) for r in rows}


def test_accepted_and_selected_is_in_prompt():
    rows = rag_trace.build_rows(
        RUN, traced=[_traced("r1")], gated=[_gated("r1")],
        prompt_keys={("rules", "c1", "r1")}, score_threshold=0.0,
    )
    assert _status(rows)["r1"] == ("in_prompt", "prompt")
    assert rows[0]["cosine"] == 0.8 and rows[0]["bm25_rank"] == 1


def test_accepted_but_cut_by_the_per_chunk_cap_is_dropped_by_cap():
    """The cap leaves no record anywhere else — this is the only trace of it."""
    rows = rag_trace.build_rows(
        RUN, traced=[_traced("r1"), _traced("r9", fused_rank=9)],
        gated=[_gated("r1"), _gated("r9")],
        prompt_keys={("rules", "c1", "r1")}, score_threshold=0.0,
    )
    st = _status(rows)
    assert st["r1"] == ("in_prompt", "prompt")
    assert st["r9"] == ("dropped_by_cap", "rule_cap")


def test_a_rejected_candidate_names_applicability_as_the_deciding_stage():
    rows = rag_trace.build_rows(
        RUN, traced=[_traced("r1")],
        gated=[_gated("r1", verdict="rejected",
                      reason="category_conflict: item=ulip scope=['term'] (C1)")],
        prompt_keys=set(), score_threshold=0.0,
    )
    assert _status(rows)["r1"] == ("rejected_applicability", "applicability")
    assert rows[0]["reason"].startswith("category_conflict")


def test_the_flat_fallback_set_is_unused_not_capped_on_a_healthy_run():
    """active_rules_fallback rows were validated but never fed to a prompt when
    RAG worked — calling that 'dropped_by_cap' would blame the wrong stage."""
    rows = rag_trace.build_rows(
        RUN, traced=[],
        gated=[_gated("r1", tier="active_rules_fallback", chunk_id=None)],
        prompt_keys=set(), score_threshold=0.0,
    )
    assert _status(rows)["r1"] == ("unused_fallback", "fallback_unused")
    assert rows[0]["fused_score"] == 0.032   # falls back to the recorded score
    assert rows[0]["cosine"] is None         # never retrieved => no leg data


def test_a_candidate_ranked_outside_the_fused_cut_never_reached_the_judge():
    rows = rag_trace.build_rows(
        RUN, traced=[_traced("r12", fused_rank=12, top_k=8)], gated=[],
        prompt_keys=set(), score_threshold=0.0,
    )
    assert _status(rows)["r12"] == ("not_retrieved_far_enough", "fusion")
    # NULL verdict is the honest value: nothing judged it.
    assert rows[0]["verdict"] is None and rows[0]["reason"] is None


def test_a_fused_survivor_under_the_retriever_threshold_is_below_threshold():
    rows = rag_trace.build_rows(
        RUN, traced=[_traced("r1", fused_score=0.004)], gated=[],
        prompt_keys=set(), score_threshold=0.01,
    )
    assert _status(rows)["r1"] == ("below_threshold", "score_threshold")


def test_a_fused_precedent_that_vanished_is_attributed_to_the_retriever_filter():
    rows = rag_trace.build_rows(
        RUN,
        traced=[_traced("p1", corpus="precedents", index="precedent_cases", category=None)],
        gated=[], prompt_keys=set(), score_threshold=0.5,
    )
    assert _status(rows)["p1"] == ("dropped_by_retriever", "retriever_filter")


def test_the_same_rule_on_two_chunks_keeps_two_distinct_score_sets():
    rows = rag_trace.build_rows(
        RUN,
        traced=[_traced("r1", chunk_id="c1", cosine=0.9),
                _traced("r1", chunk_id="c2", cosine=0.3)],
        gated=[_gated("r1", chunk_id="c1"), _gated("r1", chunk_id="c2")],
        prompt_keys={("rules", "c1", "r1")}, score_threshold=0.0,
    )
    by_chunk = {r["chunk_id"]: r for r in rows}
    assert by_chunk["c1"]["cosine"] == 0.9
    assert by_chunk["c2"]["cosine"] == 0.3
    assert by_chunk["c1"]["final_status"] == "in_prompt"
    assert by_chunk["c2"]["final_status"] == "dropped_by_cap"


def test_every_row_carries_a_status_and_a_stage():
    rows = rag_trace.build_rows(
        RUN, traced=[_traced("r1"), _traced("r2", fused_rank=99)],
        gated=[_gated("r1")], prompt_keys=set(), score_threshold=0.0,
    )
    assert len(rows) == 2
    assert all(r["final_status"] and r["deciding_stage"] for r in rows)


# --- persistence is fail-soft ----------------------------------------------

class _Savepoint:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDb:
    def __init__(self, raise_on_execute=False):
        self.raise_on_execute = raise_on_execute
        self.executed = []

    def begin_nested(self):
        return _Savepoint()

    def execute(self, stmt, rows=None):
        if self.raise_on_execute:
            raise RuntimeError('relation "retrieval_candidates" does not exist')
        self.executed.append((stmt, rows))


def test_bulk_insert_is_one_statement_for_the_whole_run():
    db = _FakeDb()
    rows = rag_trace.build_rows(
        RUN, traced=[_traced("r1"), _traced("r2", candidate_id="r2")],
        gated=[_gated("r1"), _gated("r2")], prompt_keys=set(), score_threshold=0.0,
    )
    assert rag_trace.persist_run_candidates(db, RUN, rows) == 2
    assert len(db.executed) == 1
    assert len(db.executed[0][1]) == 2


def test_a_missing_table_degrades_to_zero_rows_and_never_raises():
    db = _FakeDb(raise_on_execute=True)
    rows = rag_trace.build_rows(RUN, traced=[_traced("r1")], gated=[],
                                prompt_keys=set(), score_threshold=0.0)
    assert rag_trace.persist_run_candidates(db, RUN, rows) == 0


def test_a_run_without_an_open_run_id_is_skipped_not_inserted_under_null():
    db = _FakeDb()
    assert rag_trace.persist_run_candidates(db, None, [{"x": 1}]) == 0
    assert db.executed == []


def test_the_savepoint_is_what_keeps_a_failure_from_poisoning_the_graph_session():
    """Postgres aborts the whole transaction on a failed statement, so the
    insert must be nested — otherwise a missing table takes the analysis down
    with it."""
    class _NoSavepoint(_FakeDb):
        def begin_nested(self):
            raise AssertionError("insert must be SAVEPOINT-scoped")

    assert rag_trace.persist_run_candidates(_NoSavepoint(), RUN, [{"x": 1}]) == 0


# --- hand-off: reviewer-rejection citations ---------------------------------

class _Cit:
    def __init__(self, idx):
        self.precedent_index = idx
        self.current_text = "guaranteed returns"
        self.reviewer_comment = "misleading"
        self.confidence = 0.9
        self.satisfied_elsewhere = False
        self.action_type = "revise"
        self.evidence_needed = None


def _result(*idxs):
    return SimpleNamespace(
        citations=[_Cit(i) for i in idxs],
        rule_findings=[], novel_findings=[], product_fact_findings=[],
    )


REAL = {"id": "p-real", "violation_category": "misleading_claim", "severity": "high"}
REJECTION = {"id": "p-rej", "violation_category": "Not a violation — Disclosure",
             "severity": "informational"}


def test_a_citation_resolving_to_a_reviewer_rejection_is_dropped():
    out = nodes.map_findings_to_violations(
        _result(0, 1), [REAL, REJECTION], chunk_id="c1", chunk_index=0, location="p1"
    )
    assert [v["cited_precedent_id"] for v in out] == ["p-real"]


def test_the_guard_is_case_and_spacing_tolerant_like_the_prompt_split():
    weird = {"id": "p-x", "violation_category": "  NOT A VIOLATION — Pricing  "}
    out = nodes.map_findings_to_violations(
        _result(0), [weird], chunk_id="c1", chunk_index=0, location="p1"
    )
    assert out == []


def test_ordinary_precedent_citations_are_untouched():
    out = nodes.map_findings_to_violations(
        _result(0), [REAL], chunk_id="c1", chunk_index=0, location="p1"
    )
    assert len(out) == 1 and out[0]["severity"] == "high"


# --- hand-off: is_reviewer passthrough --------------------------------------

@pytest.mark.parametrize("flag, expected", [(True, True), (False, False), (None, False)])
def test_is_reviewer_survives_the_hit_mapping(flag, expected):
    hit = SearchHit(id="p1", score=0.5, fields={"issue_type": "x", "is_reviewer": flag})
    assert _hit_to_precedent(hit)["is_reviewer"] is expected


def test_the_reviewer_flag_is_provenance_and_never_decides_polarity():
    """is_reviewer is True for CONFIRMED reviewer findings too
    (rule_feedback_service._reviewer_precedent_row), so a polarity check that
    preferred it would read half the taught corpus backwards."""
    from app.services.preprocessing_service import _is_reviewer_rejection

    confirmed = {"is_reviewer": True, "violation_category": "misleading_claim"}
    rejected_by_ingest = {"is_reviewer": False,
                          "violation_category": "Not a violation — Disclosure"}
    assert _is_reviewer_rejection(confirmed) is False
    assert _is_reviewer_rejection(rejected_by_ingest) is True
