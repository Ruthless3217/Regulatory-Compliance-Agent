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
