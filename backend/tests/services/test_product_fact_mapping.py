# backend/tests/services/test_product_fact_mapping.py
from app.schemas.compliance_schemas import ProductFactFinding, PrecedentCitationsResult
from app.services.agents.graph.nodes import (
    _product_fact_finding_to_violation,
    map_findings_to_violations,
    dedupe_chunk_violations,
)

CARD = {"uin": "116N198V07", "product_name": "Bajaj Life eTouch II"}


def _ff(**kw):
    base = dict(
        product_index=0,
        reviewer_comment="Remove 'guaranteed returns' — eTouch is non-par term.",
        guardrail_text="Do NOT use 'guaranteed returns' language.",
        finding_kind="banned-claim",
        action_type="remove",
        current_text="guaranteed returns",
        severity="critical",
    )
    base.update(kw)
    return ProductFactFinding(**base)


def test_maps_product_fact_finding_to_violation():
    v = _product_fact_finding_to_violation(
        _ff(), CARD, chunk_id="c1", chunk_index=0, location="chunk:c1")
    assert v["category"] == "product compliance"
    assert v["severity"] == "critical"
    assert v["current_text"] == "guaranteed returns"
    assert v["violation_metadata"]["grounding"] == "product_fact"
    assert v["violation_metadata"]["product_uin"] == "116N198V07"
    assert v["violation_metadata"]["guardrail_text"].startswith("Do NOT use")


def test_map_findings_includes_product_facts():
    result = PrecedentCitationsResult(product_fact_findings=[_ff()])
    out = map_findings_to_violations(
        result, precedents=[], chunk_id="c1", chunk_index=0,
        location="chunk:c1", rules=[], product_facts=[CARD])
    assert len(out) == 1
    assert out[0]["violation_metadata"]["grounding"] == "product_fact"


def test_out_of_range_product_index_dropped():
    result = PrecedentCitationsResult(product_fact_findings=[_ff(product_index=5)])
    out = map_findings_to_violations(
        result, precedents=[], chunk_id="c1", chunk_index=0,
        location="chunk:c1", rules=[], product_facts=[CARD])
    assert out == []


def test_product_fact_outranks_other_tiers_on_dedupe():
    pf = _product_fact_finding_to_violation(
        _ff(), CARD, chunk_id="c1", chunk_index=0, location="chunk:c1")
    novel_like = {
        "current_text": "guaranteed returns",
        "severity": "critical",
        "violation_metadata": {"grounding": "novel"},
    }
    kept = dedupe_chunk_violations([novel_like, pf])
    assert len(kept) == 1
    assert kept[0]["violation_metadata"]["grounding"] == "product_fact"
