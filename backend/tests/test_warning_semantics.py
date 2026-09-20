"""A warning says WHAT was limited. It must also say WHOSE limitation it is.

The provenance audit (2026-09-17) ran the real preprocess+dispatch nodes over
every stored submission and found that three of the eight warning codes fired
on 9 of 9 documents, in both a retrievers-raising and a retrievers-healthy
mode:

    retrieval_degraded              9/9   embedding-model mismatch on every corpus
    precedent_evidence_unavailable  9/9   total accepted precedents == 0, any cause
    rule_scope_metadata_incomplete  9/9   the FLAT rule set is scope-validated
                                          before retrieval, on every run, and
                                          0/178 rules carry a product_line

None of those three says anything about the document. Yet every one of them
made the banner assert "the score covers less than the whole document", capped
the status, and blocked approval — exactly as a rider with no fact card does.

The fix is a classification, not a suppression. Every warning now carries a
`kind`:

    coverage        part of THIS document could not be product/identity grounded
    tier            an evidence tier is limited by the knowledge base
    infrastructure  a retrieval component failed

and the three surfaces that speak to a reviewer (banner, export note,
approval blocker) say the thing that is actually true for the kinds present.
Old persisted warnings have no `kind`; they are classified by code, and an
unknown code is treated as coverage — the most restrictive reading.

Two generators were also wrong about provenance and are corrected here:

  * rule_scope_metadata_incomplete counts only rejections among the document's
    ACTUAL candidates — per-chunk retrieved rules, or the flat fallback set
    only when retrieval failed and that set genuinely became the candidates.
  * the single precedent code becomes a vocabulary that tells failure, empty
    corpus, scope-rejected candidates and healthy-but-nothing-relevant apart —
    and the last of those is not a warning at all.
"""
import asyncio
import uuid
from pathlib import Path

import pytest

from app.services import analysis_warnings as aw
from app.services.agents.compliance.engine import ComplianceEngine
from app.services.agents.graph import nodes as graph_nodes
from app.services.agents.graph.context import GraphContext
from app.services.fact_card_service import FactCardService
from app.services.rag.errors import RAGDegraded
from app.services.rag.retrievers.precedent_retriever import PrecedentRetrieval

CARDS_DIR = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"


def _w(code, **detail):
    return {"code": code, "detail": detail or None}


# --------------------------------------------------------------------------
# Classification — the map, the fallback, and backward compatibility.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("code,kind", [
    ("rider_uins_without_fact_cards", "coverage"),
    ("unknown_uins", "coverage"),
    ("declared_products_without_fact_cards", "coverage"),
    ("edition_conflicts", "coverage"),
    ("product_grounding_budget", "coverage"),
    ("precedent_corpus_empty", "tier"),
    ("precedent_scope_metadata_incomplete", "tier"),
    ("rule_scope_metadata_incomplete", "tier"),
    ("precedent_evidence_unavailable", "tier"),        # legacy code on old runs
    ("retrieval_degraded", "infrastructure"),
    ("precedent_tier_unavailable", "infrastructure"),
])
def test_every_code_has_a_kind(code, kind):
    assert aw.warning_kind(_w(code)) == kind


def test_a_stored_kind_wins_over_the_code_map():
    assert aw.warning_kind({"code": "retrieval_degraded", "kind": "coverage"}) == "coverage"


def test_an_unknown_code_is_treated_as_coverage():
    """Fail-safe: a code this build has never seen gets the most restrictive
    reading, never a lighter one."""
    assert aw.warning_kind(_w("something_from_a_newer_backend")) == "coverage"


def test_a_historical_payload_without_kind_stays_readable():
    """PR #20 runs persisted warnings with no `kind`. They must classify."""
    old = [{"code": "rider_uins_without_fact_cards", "detail": {"uins": ["116N216V01"]}},
           {"code": "precedent_evidence_unavailable", "detail": {"precedents": 0}},
           {"code": "rule_scope_metadata_incomplete", "detail": {"count": 153}},
           {"code": "retrieval_degraded"}]

    assert aw.warning_kinds(old) == {"coverage", "tier", "infrastructure"}
    assert aw.evidence_coverage_state(old) == "partial"


def test_new_warnings_carry_their_kind_when_written():
    md = {}
    graph_nodes._add_warning(md, "product_grounding_budget", {"detected": 5})
    graph_nodes._add_warning(md, "retrieval_degraded")

    assert [(w["code"], w["kind"]) for w in md["analysis_warnings"]] == [
        ("product_grounding_budget", "coverage"),
        ("retrieval_degraded", "infrastructure"),
    ]


# --------------------------------------------------------------------------
# Coverage state and the reviewer-facing statement.
# --------------------------------------------------------------------------


def test_coverage_is_partial_only_when_a_coverage_warning_exists():
    assert aw.evidence_coverage_state([]) == "complete"
    assert aw.evidence_coverage_state([_w("retrieval_degraded")]) == "complete"
    assert aw.evidence_coverage_state([_w("precedent_corpus_empty")]) == "complete"
    assert aw.evidence_coverage_state([_w("rider_uins_without_fact_cards")]) == "partial"
    assert aw.evidence_coverage_state([_w("retrieval_degraded"),
                                       _w("product_grounding_budget")]) == "partial"


def test_the_statement_claims_partial_coverage_only_for_coverage_warnings():
    coverage = aw.limitation_statement([_w("rider_uins_without_fact_cards")])
    tier = aw.limitation_statement([_w("precedent_corpus_empty")])
    infra = aw.limitation_statement([_w("retrieval_degraded")])
    mixed = aw.limitation_statement([_w("retrieval_degraded"), _w("edition_conflicts")])

    assert "less than the whole document" in coverage
    assert "less than the whole document" not in tier
    assert "less than the whole document" not in infra
    assert "every section" in tier and "every section" in infra
    assert "retrieval" in infra.lower()
    assert "less than the whole document" in mixed, "coverage wins when present"


def test_no_warnings_yields_no_statement():
    assert aw.limitation_statement([]) == ""


# --------------------------------------------------------------------------
# Provenance — dispatch_node, driven through the real node.
# --------------------------------------------------------------------------


class _Q:
    def __init__(self, rows): self._rows = rows
    def filter(self, *_): return self
    def order_by(self, *_): return self
    def all(self): return self._rows


class _Db:
    def query(self, _t): return _Q([])


class _Rule:
    def __init__(self, id_, category, product_line):
        self.id, self.category, self.product_line = id_, category, product_line
        self.rule_text, self.severity, self.keywords = f"rule {id_}", "high", []


def _rules_retriever(per_chunk):
    """per_chunk: {chunk_id: {category: [rule dicts]}} or an exception."""
    class _R:
        async def retrieve_per_chunk(self, chunks=None, categories=None, **_):
            if isinstance(per_chunk, Exception):
                raise per_chunk
            return {str(c["id"]): {cat: list(per_chunk.get(str(c["id"]), {}).get(cat, []))
                                   for cat in (categories or [])} for c in chunks}
    return _R()


def _precedent_retriever(result):
    class _P:
        async def retrieve_per_chunk(self, chunks=None, **_):
            if isinstance(result, Exception):
                raise result
            return result(chunks) if callable(result) else result
    return _P()


@pytest.fixture
def dispatch(monkeypatch):
    from app.config import settings
    from app.services import fact_card_service as fcs
    from app.services import rule_generator_service as rgs
    from app.services.rag import trace as rag_trace
    from app.services.rag.retrievers import precedent_retriever as pr
    from app.services.rag.retrievers import product_docs_retriever as pdr
    from app.services.rag.retrievers import rules_retriever as rr

    cards = FactCardService(CARDS_DIR)
    monkeypatch.setattr(settings, "product_grounding_enabled", True)
    monkeypatch.setattr(fcs, "get_fact_card_service", lambda: cards)
    monkeypatch.setattr(rag_trace, "persist_run_candidates", lambda *a, **k: 0)

    class _NoPassages:
        async def retrieve(self, **_): return []

    monkeypatch.setattr(pdr, "get_product_docs_retriever", lambda: _NoPassages())

    def _run(*, active_rules, rules, precedents, product_match=None):
        monkeypatch.setattr(rgs.rule_generator_service, "get_active_rules",
                            lambda _db: active_rules)
        monkeypatch.setattr(rr, "get_rules_retriever", lambda: _rules_retriever(rules))
        monkeypatch.setattr(pr, "get_precedent_retriever", lambda: _precedent_retriever(precedents))
        chunks = [{"id": "c0", "chunk_index": 0, "text": "Guaranteed 12% returns on this ULIP."}]
        state = {
            "submission_id": str(uuid.uuid4()), "chunks": chunks,
            "metadata": {"declared_product_line": "ulip",
                         "product_match": product_match or [],
                         "product_grounding_uins": [m["uin"] for m in (product_match or [])]},
        }
        token = GraphContext.set_db_session(_Db())
        try:
            return asyncio.run(graph_nodes.dispatch_node(state))["metadata"]
        finally:
            GraphContext.reset(token)

    return _run


def _codes(md):
    return [w["code"] for w in md.get("analysis_warnings") or []]


UNTAGGED = {"claims": [_Rule("r1", "claims", None), _Rule("r2", "claims", None)]}
TAGGED = {"claims": [_Rule("r1", "claims", "ulip"), _Rule("r2", "claims", "ulip")]}
SMART_SECURE = [{"uin": "116L215V01", "product_name": "Bajaj Life Smart Secure ROP"}]


def _healthy_empty_precedents(chunks):
    return PrecedentRetrieval({str(c["id"]): [] for c in chunks},
                              source="precedent_cases", corpus_empty=False)


def test_unscoped_rules_that_were_never_candidates_raise_no_document_warning(dispatch):
    """The KB has two untagged rules. Retrieval is healthy and returned
    nothing for this chunk. Those rules never touched this document."""
    md = dispatch(active_rules=UNTAGGED, rules={}, precedents=_healthy_empty_precedents,
                  product_match=SMART_SECURE)

    assert "rule_scope_metadata_incomplete" not in _codes(md)
    assert md["scope_metadata_missing"]["count"] == 2, "the corpus fact is still recorded"


def test_a_retrieved_unscoped_rule_does_raise_the_rule_scope_warning(dispatch):
    """Now the untagged rule WAS retrieved for this chunk — it was a
    candidate, and its rejection genuinely narrowed the document's evidence."""
    per_chunk = {"c0": {"claims": [{"id": "r1", "rule_text": "x", "category": "claims",
                                    "product_line": None}]}}
    md = dispatch(active_rules=UNTAGGED, rules=per_chunk,
                  precedents=_healthy_empty_precedents, product_match=SMART_SECURE)

    w = next(x for x in md["analysis_warnings"] if x["code"] == "rule_scope_metadata_incomplete")
    assert w["kind"] == "tier"
    assert w["detail"]["tiers"] == ["chunk_rules"]
    assert w["detail"]["count"] == 1


def test_degraded_retrieval_makes_the_fallback_set_the_candidates(dispatch):
    """Retrieval failed, so the flat set IS what the document was graded
    against; its untagged rules did affect the document."""
    md = dispatch(active_rules=UNTAGGED, rules=RAGDegraded("mismatch"),
                  precedents=_healthy_empty_precedents, product_match=SMART_SECURE)

    codes = _codes(md)
    assert "retrieval_degraded" in codes
    assert "rule_scope_metadata_incomplete" in codes
    w = next(x for x in md["analysis_warnings"] if x["code"] == "rule_scope_metadata_incomplete")
    assert w["detail"]["tiers"] == ["active_rules_fallback"]


def test_retrieval_degraded_names_its_cause_and_is_infrastructure(dispatch):
    md = dispatch(active_rules=TAGGED,
                  rules=RAGDegraded("Embedding-model mismatch on rag_rules: active 'multilingual', rows 'english-v3.0'"),
                  precedents=_healthy_empty_precedents, product_match=SMART_SECURE)

    w = next(x for x in md["analysis_warnings"] if x["code"] == "retrieval_degraded")
    assert w["kind"] == "infrastructure"
    assert "Embedding-model mismatch" in w["detail"]["reason"]


def test_healthy_retrieval_with_zero_precedent_hits_is_not_a_warning(dispatch):
    """The corpus exists, retrieval worked, nothing was relevant. That is a
    complete result for this document, not a limitation of it."""
    md = dispatch(active_rules=TAGGED, rules={}, precedents=_healthy_empty_precedents,
                  product_match=SMART_SECURE)

    assert not any(c.startswith("precedent") for c in _codes(md))
    assert md["grounded_evidence"]["precedents"] == 0
    # No warning of ANY kind: this is the first configuration in which a run
    # produces an empty warning list — the provenance audit found it
    # unreachable before this change.
    assert md.get("analysis_warnings", []) == []
    assert aw.evidence_coverage_state(md.get("analysis_warnings", [])) == "complete"


def test_an_empty_precedent_corpus_is_a_tier_warning_not_coverage(dispatch):
    def _empty_corpus(chunks):
        return PrecedentRetrieval({str(c["id"]): [] for c in chunks},
                                  source="rag_compliance_examples", corpus_empty=True)

    md = dispatch(active_rules=TAGGED, rules={}, precedents=_empty_corpus,
                  product_match=SMART_SECURE)

    w = next(x for x in md["analysis_warnings"] if x["code"] == "precedent_corpus_empty")
    assert w["kind"] == "tier"
    assert md["knowledge_base_empty"] is True
    assert aw.evidence_coverage_state(md["analysis_warnings"]) == "complete"


def test_a_failed_precedent_tier_is_infrastructure(dispatch):
    def _failed(chunks):
        r = PrecedentRetrieval({str(c["id"]): [] for c in chunks},
                               source="rag_compliance_examples", corpus_empty=False)
        r.degraded_chunk_ids = {str(c["id"]) for c in chunks}
        r.degraded_reason = "Embedding-model mismatch on rag_compliance_examples"
        return r

    md = dispatch(active_rules=TAGGED, rules={}, precedents=_failed, product_match=SMART_SECURE)

    w = next(x for x in md["analysis_warnings"] if x["code"] == "precedent_tier_unavailable")
    assert w["kind"] == "infrastructure"
    assert w["detail"]["chunks_failed"] == 1
    assert "mismatch" in w["detail"]["reason"]
    assert "precedent_corpus_empty" not in _codes(md)


def test_the_retriever_raising_outright_is_infrastructure_too(dispatch):
    md = dispatch(active_rules=TAGGED, rules={}, precedents=RAGDegraded("down"),
                  product_match=SMART_SECURE)

    assert "precedent_tier_unavailable" in _codes(md)


def test_candidates_rejected_for_missing_scope_are_a_tier_warning(dispatch):
    """State D: precedents came back, every one lacked a product scope. They
    were this document's candidates, so the rejection is a real limitation —
    of the knowledge base's metadata, not of the document."""
    def _untagged(chunks):
        return PrecedentRetrieval({str(c["id"]): [
            {"id": "p1", "comment_text": "x", "product_category": None, "score": 0.9}
        ] for c in chunks}, source="rag_compliance_examples", corpus_empty=False)

    md = dispatch(active_rules=TAGGED, rules={}, precedents=_untagged, product_match=SMART_SECURE)

    w = next(x for x in md["analysis_warnings"] if x["code"] == "precedent_scope_metadata_incomplete")
    assert w["kind"] == "tier"
    assert w["detail"]["rejected"] == 1
    assert "precedent_corpus_empty" not in _codes(md)


def test_the_legacy_single_precedent_code_is_no_longer_emitted(dispatch):
    for precedents in (_healthy_empty_precedents, RAGDegraded("x")):
        md = dispatch(active_rules=TAGGED, rules={}, precedents=precedents, product_match=SMART_SECURE)
        assert "precedent_evidence_unavailable" not in _codes(md)


def test_the_grounding_budget_is_a_coverage_warning(dispatch):
    five = [{"uin": u, "product_name": n} for u, n in [
        ("116N198V07", "Bajaj Life eTouch II"), ("116A059V01", "Bajaj Life Care Plus Rider"),
        ("116B058V01", "Bajaj Life New Critical Illness Benefit Rider"),
        ("116B063V01", "Bajaj Life Accidental Death Benefit Rider II"),
        ("116B064V01", "Bajaj Life Accidental Permanent Total/Partial Disability Benefit Rider II")]]
    md = {}
    graph_nodes._add_warning(md, "product_grounding_budget",
                             {"detected": 5, "grounded": 3, "not_grounded": five[3:]})

    assert aw.warning_kind(md["analysis_warnings"][0]) == "coverage"
    assert aw.evidence_coverage_state(md["analysis_warnings"]) == "partial"


def test_a_rider_knowledge_gap_is_a_coverage_warning():
    md = {}
    graph_nodes._add_warning(md, "rider_uins_without_fact_cards",
                             {"uins": ["116N216V01"], "chunk_indexes": [24]})

    assert aw.warning_kind(md["analysis_warnings"][0]) == "coverage"


def test_mixed_warnings_keep_every_kind(dispatch):
    md = dispatch(active_rules=UNTAGGED, rules=RAGDegraded("mismatch"),
                  precedents=RAGDegraded("mismatch"), product_match=SMART_SECURE)
    graph_nodes._add_warning(md, "rider_uins_without_fact_cards", {"uins": ["116N216V01"]})

    assert aw.warning_kinds(md["analysis_warnings"]) == {"coverage", "tier", "infrastructure"}
    assert aw.evidence_coverage_state(md["analysis_warnings"]) == "partial"
    assert "less than the whole document" in aw.limitation_statement(md["analysis_warnings"])


# --------------------------------------------------------------------------
# Status and approval stay fail-safe for EVERY kind — explicitly.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("code", [
    "rider_uins_without_fact_cards",      # coverage
    "precedent_corpus_empty",             # tier
    "rule_scope_metadata_incomplete",     # tier
    "retrieval_degraded",                 # infrastructure
    "precedent_tier_unavailable",         # infrastructure
])
def test_no_kind_of_warning_can_be_recorded_as_passed(code):
    """Classification changes what the reviewer is TOLD, not whether a warned
    run can be certified. A tier warning is not a coverage claim, but a grade
    built without a tier is still not a clean pass."""
    assert ComplianceEngine.cap_status_for_warnings("passed", [_w(code)]) == "flagged"


def test_a_clean_run_is_still_passable():
    assert ComplianceEngine.cap_status_for_warnings("passed", []) == "passed"
