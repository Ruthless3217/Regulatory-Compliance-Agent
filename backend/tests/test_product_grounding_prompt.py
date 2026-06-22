from unittest.mock import MagicMock
from app.services.preprocessing_service import ContextEngineeringService

CARD = {
    "uin": "116N198V07",
    "product_name": "Bajaj Life eTouch II",
    "regulatory_descriptor": "A Non-Linked, Non-Participating, Individual Life Insurance Term Plan",
    "compliance_guardrails": {
        "claims_marketing_must_avoid": ["Do NOT use 'guaranteed returns' language."],
        "claims_marketing_must_support": ["Return of premium ONLY for the ROP variant."],
        "must_state": ["Plan UIN 116N198V07"],
    },
    "structural_flags": {"is_unit_linked": False, "is_participating": False},
    "key_terms": {"free_look_period_days": 30},
}
PASSAGE = {
    "product_name": "Bajaj Life eTouch II", "uin": "116N198V07",
    "section_path": "Benefits › Maturity", "page_number": 4,
    "text": "Return of Total Premiums Paid as Maturity Benefit applies under the Life Shield ROP variant.",
}


def _svc():
    return ContextEngineeringService(MagicMock())


def test_product_facts_tier_rendered():
    p = _svc().create_precedent_prompts(
        "Get guaranteed returns!", precedents=[], rules=[],
        product_facts=[CARD], product_passages=[])
    assert "PRODUCT FACTS" in p
    assert "Do NOT use 'guaranteed returns' language." in p
    assert "116N198V07" in p
    assert "product_fact_findings" in p


def test_brochure_passages_tier_rendered():
    p = _svc().create_precedent_prompts(
        "Get all premiums back.", precedents=[], rules=[],
        product_facts=[], product_passages=[PASSAGE])
    assert "APPROVED BROCHURE PASSAGES" in p
    assert "Life Shield ROP variant" in p


def test_tiers_omitted_when_absent():
    p = _svc().create_precedent_prompts(
        "Some copy.", precedents=[], rules=[],
        product_facts=[], product_passages=[])
    assert "PRODUCT FACTS" not in p
    assert "APPROVED BROCHURE PASSAGES" not in p


def test_no_product_args_matches_legacy_signature():
    # Back-compat: callable without the new kwargs.
    p = _svc().create_precedent_prompts("Some copy.", precedents=[], rules=[])
    assert "PRODUCT FACTS" not in p


def test_product_facts_with_no_precedents_emits_P_instruction():
    p = _svc().create_precedent_prompts(
        "Get guaranteed returns!", precedents=[], rules=[],
        product_facts=[CARD], product_passages=[])
    assert "(P) Check this section" in p
    assert "No historical precedents" in p  # novel_only still present


def test_empty_no_precedent_no_rule_omits_B_instruction():
    # Byte-identity guard: with no product and no precedent/rule, (B) must NOT
    # appear (it is subsumed by novel_only), matching pre-product-grounding output.
    p = _svc().create_precedent_prompts("Some copy.", precedents=[], rules=[])
    assert "(B) Decide" not in p
    assert "No historical precedents" in p
