import asyncio
from app.services.agents.graph.nodes import disclosure_node
from app.services.agents.compliance.scoring import ScoringService

ULIP_DOC = (
    "ULIP Plans: Grow Your Wealth While Staying Protected. "
    "A Unit Linked Insurance Plan (ULIP) is a smart way to invest while securing your family. "
    "Over the last year our flagship fund delivered strong double-digit growth. "
    "You also enjoy guaranteed loyalty additions that boost your fund value at maturity. "
    "ULIPs offer tax benefits — premiums qualify under Section 80C and maturity is tax-free under Section 10(10D). "
    "Bajaj Life Insurance Limited. CIN: U66010PN2001PLC015959. "
    "The views stated in this article are not to be construed as investment advice."
)


def test_ulip_doc_finds_core_obligations_and_caps_grade(monkeypatch):
    from pathlib import Path
    from app.services.disclaimer import registry as reg_mod
    _disclaimers_dir = Path(__file__).resolve().parents[3] / "data" / "disclaimers"
    monkeypatch.setattr("app.config.settings.disclaimers_dir", str(_disclaimers_dir))
    monkeypatch.setattr("app.config.settings.disclosure_check_enabled", True)
    # Deterministic-only run — NO LLM/Azure backstop. (past_performance, which needs
    # the backstop, is intentionally not asserted here.)
    monkeypatch.setattr("app.config.settings.disclosure_llm_backstop_enabled", False)
    reg_mod._reset_singleton_for_testing()
    try:
        # A real ULIP page has a resolved product, so has_product=True (general_product fires).
        # The dummy UIN resolves to no fact card (offline) → is_ulip stays False, so ulip_risk
        # fires via the 'ULIP' / 'unit linked' KEYWORD, not the product line.
        state = {"chunks": [{"text": ULIP_DOC, "metadata": {}}],
                 "metadata": {"product_match": [{"uin": "DUMMY-ULIP"}]}}
        out = asyncio.run(disclosure_node(state))

        ids = {v["violation_metadata"]["disclaimer_id"] for v in out["violations"]}
        # Deterministic obligations: ULIP risk, guaranteed, BOTH tax sections, general product.
        assert {"ulip_risk", "tax_123_80c", "tax_11_10_10d", "guaranteed", "general_product"} <= ids
        # Both distinct tax obligations must COEXIST (user decision 2026-06-26: collapse no
        # longer drops one of two specific tax disclaimers).
        assert "tax_123_80c" in ids and "tax_11_10_10d" in ids

        # All findings are document-level with a paste-ready verbatim fix.
        assert all(v["suggested_fix"] for v in out["violations"])

        # The page misses 5 mandated disclaimers incl. the critical ULIP-risk line →
        # the document FAILS outright (grade F). (The critical-cap that forces ≤ C is
        # exercised separately below, in the fixture where it actually binds.)
        scores = ScoringService.calculate_scores(out["violations"], db=None)
        assert scores["grade"] == "F"
        assert scores["overall"] < 60.0
        ulip = next(v for v in out["violations"] if v["violation_metadata"]["disclaimer_id"] == "ulip_risk")
        assert ulip["severity"] == "critical"
        assert "INVESTMENT RISK" in ulip["suggested_fix"]
    finally:
        reg_mod._reset_singleton_for_testing()


def test_single_critical_disclosure_binds_the_grade_cap():
    # A SINGLE missing critical disclaimer (confidence 1.0) deducts only 20 pts →
    # raw score 80 (grade B). The high-confidence-critical cap (CRITICAL_SCORE_CAP)
    # must clamp it to exactly 70 (grade C). This is the end-to-end proof that the
    # cap binds — the worked-example test never exercises it (that doc scores 42).
    from app.services.disclaimer.registry import Disclaimer
    from app.services.agents.graph.nodes import _disclosure_finding_to_violation
    d = Disclaimer(
        id="ulip_risk", type="ULIP Disclaimer",
        text="IN THIS POLICY, THE INVESTMENT RISK IN INVESTMENT PORTFOLIO IS BORNE BY THE POLICYHOLDER.",
        anchors=[], severity="critical", altered_severity="high", triggers={},
        present_threshold=0.85, altered_threshold=0.45, precedence=100, source="x",
    )
    v = _disclosure_finding_to_violation(d, status="missing", similarity=0.0,
                                         provenance="kw", confidence=1.0)
    scores = ScoringService.calculate_scores([v], db=None)
    assert scores["overall"] == 70.0   # clamped from raw 80; without the cap this grades B
    assert scores["grade"] == "C"
