"""Retrieval debugger API (/admin/retrieval) — the inspection half of corpus
curation.

The routes are thin; the load-bearing logic is (a) deciding a run genuinely has
no retrieval data instead of returning a clean-looking empty payload, (b) the
grouping, and (c) enrichment that must degrade to a bare id rather than 500.
Those are exercised directly — no live DB (the Postgres-only UUID/JSONB column
types break on sqlite), following tests/test_reviewer_actions.py's FakeSession.
"""
import uuid

import pytest

from app.api.routes import admin_retrieval as ar
from app.auth.permissions import role_has
from app.models.rule import Rule


# --- fakes -----------------------------------------------------------------

class _FakeRuleQuery:
    def __init__(self, rows):
        self._rows = rows
        self._ids = None

    def filter(self, expr):
        # Rule.id.in_([...]) — pull the bound id list back out.
        self._ids = {str(v) for v in expr.right.value}
        return self

    def all(self):
        return [r for r in self._rows if self._ids is None or str(r.id) in self._ids]


class FakeSession:
    """Enough Session for enrich(): a Rule query and a raw precedent SELECT."""

    def __init__(self, rules=(), precedents=(), rules_raise=False, precedents_raise=False):
        self._rules = list(rules)
        self._precedents = list(precedents)
        self._rules_raise = rules_raise
        self._precedents_raise = precedents_raise

    def query(self, *_cols):
        if self._rules_raise:
            raise RuntimeError("rules table is gone")
        return _FakeRuleQuery(self._rules)

    def execute(self, _stmt, params):
        if self._precedents_raise:
            raise RuntimeError("precedent_cases does not exist")
        wanted = set(params["ids"])
        rows = [p for p in self._precedents if str(p["id"]) in wanted]
        return _FakeResult(rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Run:
    def __init__(self, run_metadata=None, **kw):
        self.id = kw.get("id", uuid.uuid4())
        self.submission_id = kw.get("submission_id", uuid.uuid4())
        self.run_number = kw.get("run_number", 1)
        self.status = kw.get("status", "completed")
        self.degraded_reason = kw.get("degraded_reason")
        self.started_at = None
        self.run_metadata = run_metadata


RULE_ID = uuid.uuid4()
PREC_ID = uuid.uuid4()
GHOST_ID = uuid.uuid4()  # deleted referent — must degrade, never 500


def _real_metadata():
    """Shape written by graph/nodes.py dispatch → engine.run_metadata_from_state."""
    return {
        "product_match": [{"uin": "116N165V01", "product_name": "Saral Jeevan"}],
        "rag_degraded": False,
        "retrieval_debug": {
            "scope": {"uins": ["116N165V01"], "categories": ["term"], "resolved": True},
            "candidates_total": 4,
            "rejected_total": 2,
            "rejected": [
                {"corpus": "rules", "id": str(RULE_ID), "score": 0.71,
                 "scope_value": "ulip", "verdict": "rejected",
                 "reason": "category_conflict: item=ulip scope=['term'] (C1)",
                 "tier": "chunk_rules", "chunk_id": "c1"},
                {"corpus": "precedents", "id": str(GHOST_ID), "score": 0.64,
                 "scope_value": "pension", "verdict": "rejected",
                 "reason": "category_conflict: item=pension_annuity scope=['term'] (C1)",
                 "tier": "precedents", "chunk_id": "c1"},
            ],
            "accepted_sample": [
                {"corpus": "precedents", "id": str(PREC_ID), "score": 0.88,
                 "scope_value": "term", "verdict": "accepted",
                 "reason": "category_match: term", "tier": "precedents",
                 "chunk_id": "c1"},
                {"corpus": "rules", "id": str(uuid.uuid4()), "score": None,
                 "scope_value": None, "verdict": "accepted",
                 "reason": "global_untagged: untagged (C2/C7)",
                 "tier": "active_rules_fallback"},
            ],
        },
    }


def _session():
    return FakeSession(
        rules=[Rule(id=RULE_ID, rule_text="ULIP charges must be disclosed." * 40,
                    category="regulatory", severity="high", product_line="ulip")],
        precedents=[{"id": str(PREC_ID), "issue_type": "missing_disclaimer",
                     "highlighted_span": "guaranteed returns", "source_file": "batch7.xlsx",
                     "product_category": "term", "severity": "high"}],
    )


# --- permission ------------------------------------------------------------

def test_gated_on_a_curation_scope_never_granted_to_a_plain_user():
    assert not role_has("user", "rules:write")
    assert role_has("admin", "rules:write")
    assert role_has("super_admin", "rules:write")


def test_router_is_registered_under_admin_retrieval():
    from app.main import app
    paths = {r.path for r in app.routes}
    assert "/admin/retrieval/runs/{run_id}" in paths
    assert "/admin/retrieval/runs/{run_id}/rejected" in paths
    assert "/admin/retrieval/submissions/{submission_id}/latest" in paths
    # Per-candidate trace (migration 0037) — same router, same auth.
    assert "/admin/retrieval/runs/{run_id}/candidates" in paths
    assert "/admin/retrieval/runs/{run_id}/chunks" in paths


# --- honesty: runs with no retrieval data ----------------------------------

@pytest.mark.parametrize("md", [None, {}, "not-a-dict"])
def test_null_run_metadata_is_reported_as_no_retrieval_data(md):
    dbg, reason = ar.extract_debug(_Run(run_metadata=md))
    assert dbg is None
    assert "run_metadata" in reason and "0022" in reason


def test_metadata_without_a_retrieval_debug_block_is_no_retrieval_data():
    dbg, reason = ar.extract_debug(_Run(run_metadata={"grounding_mix": {"precedent": 1}}))
    assert dbg is None
    assert "dispatch" in reason


def test_zero_recorded_candidates_is_no_retrieval_data_not_a_clean_run():
    run = _Run(run_metadata={"retrieval_debug": {
        "scope": {"categories": [], "resolved": False},
        "candidates_total": 0, "rejected_total": 0,
        "rejected": [], "accepted_sample": [],
    }})
    dbg, reason = ar.extract_debug(run)
    assert dbg is None
    assert "zero candidates" in reason
    assert ar._no_data(run, reason)["status"] == "no_retrieval_data"


def test_truncated_run_with_only_acceptances_still_reads_as_data():
    dbg, reason = ar.extract_debug(_Run(run_metadata=_real_metadata()))
    assert reason is None
    assert dbg["candidates_total"] == 4


# --- the full story --------------------------------------------------------

def test_story_groups_candidates_by_corpus_and_tier_with_scope_and_counts():
    run = _Run(run_metadata=_real_metadata())
    dbg, _ = ar.extract_debug(run)
    out = ar._story(run, dbg, _session())

    assert out["status"] == "ok"
    assert out["scope"]["categories"] == ["term"]
    assert out["product_match"][0]["uin"] == "116N165V01"
    assert out["by_corpus_recorded"] == {
        "rules": {"accepted": 1, "rejected": 1},
        "precedents": {"accepted": 1, "rejected": 1},
    }
    assert set(out["candidates"]["rules"]) == {"chunk_rules", "active_rules_fallback"}
    assert list(out["candidates"]["precedents"]) == ["precedents"]

    rec = out["candidates"]["rules"]["chunk_rules"][0]
    assert rec["score"] == 0.71 and rec["scope_value"] == "ulip"
    assert rec["verdict"] == "rejected" and rec["reason"].startswith("category_conflict")


def test_story_reports_truncation_instead_of_implying_the_sample_is_everything():
    md = _real_metadata()
    md["retrieval_debug"]["candidates_total"] = 900  # 4 recorded, 900 seen
    run = _Run(run_metadata=md)
    dbg, _ = ar.extract_debug(run)
    totals = ar._story(run, dbg, _session())["totals"]

    assert totals["candidates_total"] == 900
    assert totals["records_available"] == 4
    assert totals["truncated"] is True
    assert "capped" in totals["note"]
    # dispatch_node caps rejections too (_rejected[:100]); a note claiming they
    # are all persisted is what let a 100-row sample be read as the population.
    assert "All rejections are persisted" not in totals["note"]


def test_the_two_populations_never_add_up_to_more_than_the_run_judged():
    """The counters must reconcile: judged = rejected + accepted, both counting
    the whole run. Deriving accepted from records_available instead mixes the
    populations — it counts every rejection the 100-cap dropped as an
    acceptance, which is how the inspector once showed 3505 + 5318 > 5518."""
    md = _real_metadata()
    md["retrieval_debug"]["candidates_total"] = 900
    md["retrieval_debug"]["rejected_total"] = 700
    dbg, _ = ar.extract_debug(_Run(run_metadata=md))
    t = ar._totals(dbg, ar._records(dbg))

    accepted_total = t["candidates_total"] - t["rejected_total"]
    assert accepted_total == 200
    assert t["rejected_total"] + accepted_total == t["candidates_total"]
    # The sample is strictly inside the population it was drawn from.
    assert t["recorded_rejected"] <= t["rejected_total"]
    assert t["records_available"] - t["recorded_rejected"] <= accepted_total


def test_story_never_mutates_the_persisted_run_metadata():
    run = _Run(run_metadata=_real_metadata())
    dbg, _ = ar.extract_debug(run)
    ar._story(run, dbg, _session())
    persisted = run.run_metadata["retrieval_debug"]["rejected"][0]
    assert "document" not in persisted  # enrichment happened on copies


# --- rejected grouping -----------------------------------------------------

def test_rejected_grouping_buckets_by_reason_code_not_the_full_reason_string():
    records = ar._records(_real_metadata()["retrieval_debug"])
    rejected = [r for r in records if r["verdict"] == "rejected"]
    grouped = ar.group_by_reason(rejected)

    assert list(grouped) == ["category_conflict"]
    assert grouped["category_conflict"]["count"] == 2
    # the verbose reason is still on each candidate
    assert "scope=['term']" in grouped["category_conflict"]["candidates"][0]["reason"]


def test_distinct_reason_codes_get_distinct_buckets():
    grouped = ar.group_by_reason([
        {"reason": "category_conflict: a (C1)"},
        {"reason": "category_conflict: b (C1)"},
        {"reason": "scope_unresolved: nothing rejected (C3)"},
        {"reason": None},
    ])
    assert grouped["category_conflict"]["count"] == 2
    assert grouped["scope_unresolved"]["count"] == 1
    assert grouped["unknown"]["count"] == 1


# --- enrichment ------------------------------------------------------------

def test_enrichment_names_the_document_behind_each_id():
    records = ar._records(_real_metadata()["retrieval_debug"])
    assert ar.enrich(_session(), records) == {}

    by_id = {r["id"]: r for r in records}
    assert by_id[str(PREC_ID)]["document"]["issue_type"] == "missing_disclaimer"
    assert by_id[str(PREC_ID)]["document"]["source_file"] == "batch7.xlsx"
    rule_doc = by_id[str(RULE_ID)]["document"]
    assert rule_doc["product_line"] == "ulip"
    assert len(rule_doc["rule_text"]) == ar._PREVIEW  # long text is previewed


def test_a_deleted_referent_degrades_to_the_bare_id():
    records = ar._records(_real_metadata()["retrieval_debug"])
    ar.enrich(_session(), records)
    ghost = next(r for r in records if r["id"] == str(GHOST_ID))
    assert ghost["document"] is None
    assert ghost["id"] == str(GHOST_ID)  # still curatable


def test_a_failing_join_degrades_with_a_note_instead_of_raising():
    records = ar._records(_real_metadata()["retrieval_debug"])
    notes = ar.enrich(FakeSession(rules_raise=True, precedents_raise=True), records)

    assert "rules" in notes and "precedents" in notes
    assert all(r["document"] is None for r in records)
    assert all(r["id"] for r in records)


def test_non_uuid_ids_are_skipped_not_passed_to_postgres():
    # Fallback-index hits can carry a non-uuid id; it must not poison the query.
    records = [{"corpus": "rules", "id": "not-a-uuid", "verdict": "accepted"},
               {"corpus": "rules", "id": "None", "verdict": "accepted"}]
    assert ar.enrich(_session(), records) == {}
    assert all(r["document"] is None for r in records)


# ===========================================================================
# Per-candidate trace endpoints (migration 0037)
# ===========================================================================

import asyncio  # noqa: E402


def _trace_row(**kw):
    base = {
        "chunk_id": "c1", "corpus": "rules", "tier": "chunk_rules",
        "category": "regulatory", "candidate_id": str(RULE_ID),
        "retrieval_method": "both", "cosine": 0.82, "ts_rank": 0.13,
        "vector_rank": 1, "bm25_rank": 2, "fused_score": 0.032, "fused_rank": 1,
        "filters": {"index": "rag_rules", "applied": {"is_active": True}},
        "scope_value": "term", "verdict": "accepted",
        "reason": "category_match: term", "final_status": "in_prompt",
        "deciding_stage": "prompt",
    }
    base.update(kw)
    return base


class FakeTraceSession(FakeSession):
    """FakeSession + the retrieval_candidates queries, filtered in Python so
    pagination and facets are genuinely exercised rather than stubbed."""

    def __init__(self, rows=(), cited=(), **kw):
        super().__init__(**kw)
        self._trace_rows = list(rows)
        self._cited = list(cited)
        self.run = _Run()

    def get(self, _model, _id):
        return self.run

    def _match(self, params, skip=None):
        out = []
        for r in self._trace_rows:
            if all(
                params.get(k) in (None, "") or k == skip or r.get(k) == params.get(k)
                for k in ar._TRACE_FILTERS
            ):
                out.append(r)
        return out

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = params or {}
        if "FROM precedent_cases" in sql:
            return super().execute(stmt, params)
        if "FROM violations" in sql:
            return _FakeResult([{"corpus": c, "id": i} for c, i in self._cited])
        if "GROUP BY final_status" in sql:
            buckets = {}
            for r in self._match(params, skip="final_status"):
                key = (r["final_status"], r["deciding_stage"])
                buckets[key] = buckets.get(key, 0) + 1
            return _FakeResult([
                {"final_status": s, "deciding_stage": d, "n": n}
                for (s, d), n in buckets.items()
            ])
        if "GROUP BY chunk_id, corpus" in sql:
            buckets = {}
            for r in self._trace_rows:
                b = buckets.setdefault((r["chunk_id"], r["corpus"]), {
                    "chunk_id": r["chunk_id"], "corpus": r["corpus"], "candidates": 0,
                    "accepted": 0, "in_prompt": 0, "rejected_applicability": 0,
                    "dropped_by_cap": 0, "top_fused_score": None,
                })
                b["candidates"] += 1
                b["accepted"] += r["verdict"] == "accepted"
                for st in ("in_prompt", "rejected_applicability", "dropped_by_cap"):
                    b[st] += r["final_status"] == st
                if r["fused_score"] is not None:
                    b["top_fused_score"] = max(b["top_fused_score"] or 0, r["fused_score"])
            return _FakeResult(list(buckets.values()))
        if "COUNT(*) AS n FROM retrieval_candidates" in sql:
            n = len(self._trace_rows) if " AND " not in sql else len(self._match(params))
            return _FakeResult([{"n": n}])
        rows = self._match(params)
        start = params.get("offset", 0)
        return _FakeResult(rows[start:start + params.get("limit", 25)])


def _candidates(session, **kw):
    # limit/offset default to fastapi Query() objects, which only resolve to
    # ints through the DI machinery — supply them when calling the coroutine
    # directly.
    kw.setdefault("limit", 25)
    kw.setdefault("offset", 0)
    return asyncio.run(ar.run_candidates(run_id=str(session.run.id), db=session, **kw))


# --- honesty: a run with no trace ------------------------------------------

def test_a_run_with_no_traced_candidates_says_so_instead_of_showing_zeros():
    out = _candidates(FakeTraceSession(rows=[]))
    assert out["status"] == "no_retrieval_data"
    assert "0037" in out["reason"]
    # and it must not be mistaken for "retrieval returned nothing"
    assert "NOT 'nothing was retrieved'" in out["reason"]


def test_a_missing_trace_table_degrades_instead_of_500ing():
    class _Broken(FakeTraceSession):
        def execute(self, stmt, params=None):
            raise RuntimeError("relation retrieval_candidates does not exist")

    out = _candidates(_Broken())
    assert out["status"] == "no_retrieval_data"
    assert out["detail"] == "RuntimeError"


def test_the_chunk_rollup_uses_the_same_no_data_contract():
    session = FakeTraceSession(rows=[])
    out = asyncio.run(ar.run_chunks(run_id=str(session.run.id), db=session))
    assert out["status"] == "no_retrieval_data"


# --- pagination -------------------------------------------------------------

def test_pagination_returns_a_window_and_the_full_matched_count():
    rows = [_trace_row(candidate_id=f"r{i}", fused_rank=i) for i in range(60)]
    out = _candidates(FakeTraceSession(rows=rows), limit=25, offset=25)

    assert out["status"] == "ok"
    assert out["total_traced"] == 60
    assert out["matched"] == 60
    assert len(out["rows"]) == 25
    assert out["rows"][0]["candidate_id"] == "r25"
    assert (out["limit"], out["offset"]) == (25, 25)


def test_filters_narrow_the_matched_count_not_just_the_page():
    rows = (
        [_trace_row(candidate_id=f"a{i}", chunk_id="c1") for i in range(10)]
        + [_trace_row(candidate_id=f"b{i}", chunk_id="c2") for i in range(5)]
    )
    out = _candidates(FakeTraceSession(rows=rows), chunk_id="c2")
    assert out["total_traced"] == 15      # the run
    assert out["matched"] == 5            # the filter
    assert out["filters"]["chunk_id"] == "c2"


def test_every_documented_filter_dimension_is_wired():
    rows = [
        _trace_row(candidate_id="a", corpus="rules", verdict="accepted",
                   final_status="in_prompt"),
        _trace_row(candidate_id="b", corpus="precedents", verdict="rejected",
                   final_status="rejected_applicability"),
    ]
    assert _candidates(FakeTraceSession(rows=rows), corpus="precedents")["matched"] == 1
    assert _candidates(FakeTraceSession(rows=rows), verdict="rejected")["matched"] == 1
    assert _candidates(
        FakeTraceSession(rows=rows), final_status="in_prompt"
    )["matched"] == 1


# --- facets -----------------------------------------------------------------

def test_facets_count_both_dimensions_over_the_run():
    rows = (
        [_trace_row(candidate_id=f"a{i}") for i in range(3)]
        + [_trace_row(candidate_id=f"b{i}", final_status="dropped_by_cap",
                      deciding_stage="rule_cap") for i in range(2)]
        + [_trace_row(candidate_id="c", final_status="rejected_applicability",
                      deciding_stage="applicability", verdict="rejected")]
    )
    facets = _candidates(FakeTraceSession(rows=rows))["facets"]
    assert facets["final_status"] == {
        "in_prompt": 3, "dropped_by_cap": 2, "rejected_applicability": 1
    }
    assert facets["deciding_stage"] == {"prompt": 3, "rule_cap": 2, "applicability": 1}


def test_filtering_by_a_status_does_not_zero_out_the_other_status_facets():
    """A facet filtered by its own value is one bucket, which is not a facet —
    the point of the panel is to show what you are NOT looking at."""
    rows = [
        _trace_row(candidate_id="a"),
        _trace_row(candidate_id="b", final_status="dropped_by_cap",
                   deciding_stage="rule_cap"),
    ]
    out = _candidates(FakeTraceSession(rows=rows), final_status="in_prompt")
    assert out["matched"] == 1
    assert out["facets"]["final_status"] == {"in_prompt": 1, "dropped_by_cap": 1}


def test_a_facet_still_respects_the_other_filters():
    rows = [
        _trace_row(candidate_id="a", chunk_id="c1"),
        _trace_row(candidate_id="b", chunk_id="c2", final_status="dropped_by_cap",
                   deciding_stage="rule_cap"),
    ]
    out = _candidates(FakeTraceSession(rows=rows), chunk_id="c1")
    assert out["facets"]["final_status"] == {"in_prompt": 1}


# --- used_in_final_verdict --------------------------------------------------

def test_used_in_final_verdict_is_computed_from_the_violation_join():
    rows = [_trace_row(candidate_id=str(RULE_ID)),
            _trace_row(candidate_id=str(PREC_ID), corpus="precedents")]
    session = FakeTraceSession(rows=rows, cited=[("precedents", str(PREC_ID))])
    out = _candidates(session)

    used = {r["candidate_id"]: r["used_in_final_verdict"] for r in out["rows"]}
    assert used[str(PREC_ID)] is True
    assert used[str(RULE_ID)] is False   # accepted, in prompt, never cited


def test_the_corpus_is_part_of_the_citation_key():
    """rule_id and cited_precedent_id are different namespaces; a bare id match
    would mark a rule 'used' because a precedent happened to share the uuid."""
    same = str(RULE_ID)
    session = FakeTraceSession(
        rows=[_trace_row(candidate_id=same, corpus="rules")],
        cited=[("precedents", same)],
    )
    assert _candidates(session)["rows"][0]["used_in_final_verdict"] is False


def test_a_dead_violation_join_degrades_to_false_rather_than_500ing():
    class _NoViolations(FakeTraceSession):
        def execute(self, stmt, params=None):
            if "FROM violations" in str(stmt):
                raise RuntimeError("violations is gone")
            return super().execute(stmt, params)

    out = _candidates(_NoViolations(rows=[_trace_row()]))
    assert out["rows"][0]["used_in_final_verdict"] is False


# --- enrichment reuse -------------------------------------------------------

def test_traced_rows_are_enriched_through_the_same_path_as_sampled_ones():
    session = FakeTraceSession(rows=[_trace_row(candidate_id=str(RULE_ID))])
    session._rules = _session()._rules
    row = _candidates(session)["rows"][0]
    assert row["id"] == str(RULE_ID)             # sampled-shape alias
    assert row["document"]["product_line"] == "ulip"


# --- chunk rollup -----------------------------------------------------------

def test_chunk_rollup_totals_each_chunk_and_keeps_the_corpus_split():
    rows = [
        _trace_row(chunk_id="c1", corpus="rules", candidate_id="r1"),
        _trace_row(chunk_id="c1", corpus="rules", candidate_id="r2",
                   final_status="dropped_by_cap", deciding_stage="rule_cap"),
        _trace_row(chunk_id="c1", corpus="precedents", candidate_id="p1",
                   verdict="rejected", final_status="rejected_applicability",
                   fused_score=0.05),
        _trace_row(chunk_id="c2", corpus="rules", candidate_id="r3"),
    ]
    session = FakeTraceSession(rows=rows)
    out = asyncio.run(ar.run_chunks(run_id=str(session.run.id), db=session))

    assert out["status"] == "ok"
    assert out["chunks_total"] == 2 and out["candidates_total"] == 4
    c1 = out["chunks"][0]
    assert c1["chunk_id"] == "c1"            # heaviest chunk first
    assert c1["candidates"] == 3 and c1["accepted"] == 2
    assert c1["in_prompt"] == 1 and c1["dropped_by_cap"] == 1
    assert c1["rejected_applicability"] == 1
    assert c1["top_fused_score"] == 0.05     # max across both corpora
    assert set(c1["by_corpus"]) == {"rules", "precedents"}
    assert c1["by_corpus"]["precedents"]["candidates"] == 1


def test_the_chunkless_fallback_bucket_sorts_last_and_is_not_dropped():
    out = ar.chunk_rollup([
        {"chunk_id": None, "corpus": "rules", "candidates": 99, "accepted": 99,
         "in_prompt": 0, "rejected_applicability": 0, "dropped_by_cap": 0,
         "top_fused_score": None},
        {"chunk_id": "c1", "corpus": "rules", "candidates": 3, "accepted": 3,
         "in_prompt": 3, "rejected_applicability": 0, "dropped_by_cap": 0,
         "top_fused_score": 0.03},
    ])
    assert [c["chunk_id"] for c in out] == ["c1", None]
    assert out[1]["candidates"] == 99


# --- filter plumbing --------------------------------------------------------

def test_only_whitelisted_dimensions_reach_the_sql_and_always_as_parameters():
    clause = ar.trace_where({"chunk_id": "c1", "corpus": "rules", "verdict": None,
                             "final_status": "", "evil": "; DROP TABLE"})
    assert clause == " AND chunk_id = :chunk_id AND corpus = :corpus"
    assert "evil" not in clause and "DROP" not in clause


def test_the_skipped_dimension_is_left_out_of_the_facet_clause():
    params = {"chunk_id": "c1", "final_status": "in_prompt"}
    assert ar.trace_where(params, skip="final_status") == " AND chunk_id = :chunk_id"
