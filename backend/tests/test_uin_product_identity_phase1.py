"""UIN is regulatory evidence, not a product key.

Phase 1 repairs five places where the two were conflated, without introducing
the Product/ProductVariant/ProductIdentifier schema. Every failure below was
reproduced against the real corpus before the fix (forensic audit 2026-09-11):

  F1  brochure_parser took `uins[0]` — the first UIN in DOCUMENT ORDER — as the
      plan UIN. Brochures list their riders early, so 14 of 49 ingested
      documents carry a wrong primary UIN; five different products were all
      stamped 116A057V02 (a rider).
  F3  eTouch II (116N198V07) therefore has 0 retrievable passages: its 58
      vectors are stamped 116B056V01.
  F4  FactCardService._by_uin.setdefault(rider, parent_card) made
      get("116N216V01") return "Bajaj Life Smart Secure ROP" — a rider silently
      inheriting its parent's guardrails, which is exactly the false grounding
      the rider gate exists to prevent.
  F5  116L214V01 maps to three variant cards whose guardrails DISAGREE
      (offers_guaranteed_benefits False on one, True on two). get() returned
      whichever sorted last — the permissive one.
  F6  run_context_fingerprint hashed a fact card's UIN and not its content, so
      re-curating a card reused every cached chunk verdict. Rules were already
      content-hashed; only products were not.
  F7  _hit_to_precedent_legacy emitted no product_category at all, so the
      applicability judge rejected all 5,229 legacy precedents as "untagged"
      rather than as a declared absence.

Role is decided by what the corpus STATES — product_category == "rider",
the rider_uins[] graph, and the IRDAI regulatory_descriptor — never by the
UIN's category letter. 116N216V01 is an N-lettered rider, so the letter is
not an invariant.
"""
import json
from pathlib import Path

import pytest

from app.services.brochure_parser import UinEvidence, select_primary_uin
from app.services.fact_card_service import FactCardService

CARDS_DIR = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"


@pytest.fixture(scope="module")
def cards():
    svc = FactCardService(CARDS_DIR)
    assert not svc.availability_issues, svc.availability_issues
    return svc


def _ev(value, offset, line, page=1):
    return UinEvidence(value=value, page=page, char_offset=offset, line_text=line)


# --------------------------------------------------------------------------
# C1 — the parser selects by evidence, never by position.
# --------------------------------------------------------------------------

DESCRIPTOR = ("A Unit-Linked Non-Participating Individual Life Savings "
              "Insurance Plan")
# The real shape of the failure: riders are cited on page 2, the plan's own UIN
# sits in the page-1 descriptor line. Document order puts the rider first.
RIDER_FIRST_LINE = "Riders available: Linked Accident Protection (UIN: 116A057V02)"
PLAN_LINE = f"Bajaj Life Smart Secure ROP - {DESCRIPTOR} (UIN:116L215V01)"


def test_the_first_uin_in_the_document_no_longer_wins():
    """The whole of F1 in one assertion."""
    evidence = [
        _ev("116A057V02", 10, RIDER_FIRST_LINE),
        _ev("116L215V01", 900, PLAN_LINE, page=1),
    ]

    uin, reason = select_primary_uin(
        evidence, descriptor=DESCRIPTOR, product_name="Bajaj Life Smart Secure ROP",
        full_text=RIDER_FIRST_LINE + "\n" + ("filler " * 120) + PLAN_LINE,
    )

    assert uin == "116L215V01"
    assert reason == "descriptor_local"


def test_a_uin_inside_the_regulatory_descriptor_line_wins():
    """The descriptor is the IRDAI-mandated page-1 identity line and is present
    on 44/44 curated cards — the strongest evidence the document carries."""
    evidence = [_ev("116N021V08", 5, "Group Term Life riders"),
                _ev("116L215V01", 60, PLAN_LINE)]

    uin, reason = select_primary_uin(
        evidence, descriptor=DESCRIPTOR, product_name="Something Unrelated",
        full_text="Group Term Life riders\n" + PLAN_LINE,
    )

    assert (uin, reason) == ("116L215V01", "descriptor_local")


def test_without_a_descriptor_the_uin_nearest_the_product_name_wins():
    name = "Bajaj Life eTouch II"
    text = (f"Riders: Family Protect (UIN: 116B056V01)\n"
            f"{name} (UIN:116N198V07) is a term plan.")
    evidence = [_ev("116B056V01", text.index("116B056V01"), "Riders: Family Protect"),
                _ev("116N198V07", text.index("116N198V07"), f"{name} (UIN:116N198V07)")]

    uin, reason = select_primary_uin(
        evidence, descriptor=None, product_name=name, full_text=text,
    )

    assert (uin, reason) == ("116N198V07", "name_local")


def test_a_uin_far_from_every_name_mention_is_not_evidence():
    """Proximity is the point. A UIN quoted 4,000 characters from any mention of
    the product is a citation of something else."""
    name = "Bajaj Life eTouch II"
    text = f"{name} is a term plan.\n" + ("filler " * 800) + "(UIN: 116B056V01)"
    evidence = [_ev("116B056V01", text.index("116B056V01"), "(UIN: 116B056V01)")]

    uin, reason = select_primary_uin(
        evidence, descriptor=None, product_name=name, full_text=text,
    )

    assert uin is None
    assert reason == "role_unresolved"


def test_no_resolvable_uin_fabricates_nothing():
    evidence = [_ev("116A057V02", 3, "Riders available"),
                _ev("116B034V02", 40, "Also available")]

    uin, reason = select_primary_uin(
        evidence, descriptor=None, product_name="", full_text="Riders available",
    )

    assert uin is None
    assert reason == "role_unresolved"


def test_a_document_with_no_uin_at_all_is_unresolved():
    assert select_primary_uin([], descriptor=DESCRIPTOR, product_name="X",
                              full_text="") == (None, "role_unresolved")


def test_every_secondary_uin_survives_selection():
    """Riders must stay discoverable; only the PRIMARY choice changes."""
    evidence = [
        _ev("116A057V02", 10, RIDER_FIRST_LINE),
        _ev("116A059V01", 30, RIDER_FIRST_LINE),
        _ev("116L215V01", 900, PLAN_LINE),
    ]

    uin, _ = select_primary_uin(
        evidence, descriptor=DESCRIPTOR, product_name="Bajaj Life Smart Secure ROP",
        full_text=RIDER_FIRST_LINE + "\n" + ("filler " * 120) + PLAN_LINE,
    )

    assert uin == "116L215V01"
    assert [e.value for e in evidence] == ["116A057V02", "116A059V01", "116L215V01"]


def test_evidence_carries_page_offset_and_line():
    e = _ev("116L215V01", 900, PLAN_LINE, page=2)

    assert (e.value, e.page, e.char_offset) == ("116L215V01", 2, 900)
    assert "116L215V01" in e.line_text


def test_the_real_smart_secure_combi_line_selects_the_plan_not_the_component():
    """Verbatim from the GEO Smart Secure disclaimers block: both UINs on one
    line, the health component named second."""
    line = ("(1) Bajaj Life Invest Protect Goal Plus - Elite Variant - "
            f"{DESCRIPTOR} (UIN:116L215V01). (2) Bajaj Life Secure Plus - "
            "Shield with ROP Variant - A Non-Participating, Non-Linked, "
            "Individual Health Plan (UIN:116N216V01).")
    evidence = [_ev("116L215V01", line.index("116L215V01"), line),
                _ev("116N216V01", line.index("116N216V01"), line)]

    uin, reason = select_primary_uin(
        evidence, descriptor=DESCRIPTOR,
        product_name="Bajaj Life Invest Protect Goal Plus", full_text=line,
    )

    assert uin == "116L215V01", "the ULIP base, not the health component"
    assert reason == "descriptor_local"


# --------------------------------------------------------------------------
# C2 — a rider never inherits its parent's card; a collision is never silent.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rider", [
    "116B036V02",  # cited by six different parents
    "116B061V01",
])
def test_a_rider_uin_never_returns_a_parent_fact_card(rider, cards):
    assert cards.get(rider) is None
    assert cards.get_all(rider) == []
    assert cards.lookup_many([rider]) == []


def test_the_parent_relationship_is_still_available_explicitly(cards):
    """Removing the implicit fallback must not destroy the information — it
    must stop it being returned as if it were the rider's OWN card."""
    parents = cards.parents_of_rider("116N216V01")

    assert [p["uin"] for p in parents] == ["116L215V01"]
    assert cards.parents_of_rider("116L215V01") == [], "a plan is nobody's rider"


def test_the_rider_gate_still_reports_the_gap(cards):
    # 116N216V01 (Secure Plus) got its own card on 2026-09-18 and left this
    # list; 116B036V02 is still cited by six parents and has no card of its own.
    assert "116B036V02" in cards.rider_uins_without_fact_cards
    assert "116N216V01" not in cards.rider_uins_without_fact_cards
    assert "116N216V01" in cards.known_uins and "116B036V02" in cards.known_uins


def test_a_cited_component_with_its_own_card_is_its_own_product(cards):
    """The GEO Smart Secure health component. The Smart Secure card still
    cites it (rider_uins), but a lookup returns Secure Plus's OWN card — never
    the parent's — and the parent link is still explicit."""
    card = cards.get("116N216V01")

    assert card["product_name"] == "Bajaj Life Secure Plus"
    assert card["product_category"] == "health"
    assert [p["uin"] for p in cards.parents_of_rider("116N216V01")] == ["116L215V01"]


def test_a_colliding_uin_never_silently_resolves_to_one_card(cards):
    """116L214V01's three variants disagree on offers_guaranteed_benefits, so
    the silently-chosen card decided whether 'guaranteed' wording was legal."""
    resolution = cards.resolve_one("116L214V01")

    assert resolution.ambiguous is True
    assert resolution.card is None
    assert len(resolution.candidates) == 3
    assert resolution.reason == "ambiguous_variants"
    assert cards.get("116L214V01") is None
    assert cards.lookup_many(["116L214V01"]) == []


def test_every_colliding_candidate_is_preserved(cards):
    names = {c["product_name"] for c in cards.resolve_one("116L214V01").candidates}

    assert names == {
        "Bajaj Life Smart Wealth Goal VI",
        "Bajaj Life Smart Wealth Goal VI (Joint Life Wealth variant)",
        "Bajaj Life Smart Wealth Goal VI (Wealth Variant)",
    }
    assert len(cards.get_all("116L214V01")) == 3


def test_the_two_supreme_variants_are_both_surfaced(cards):
    resolution = cards.resolve_one("116L211V02")

    assert resolution.ambiguous is True
    assert {c["product_name"] for c in resolution.candidates} == {
        "Bajaj Life Supreme - Gold", "Bajaj Life Supreme - Horizon",
    }


def test_the_116n198_filing_versions_are_not_merged(cards):
    """Same base UIN, two cards, contradictory offers_maturity_benefit. Phase 1
    must leave them distinct — merging them is a curation decision."""
    v5, v7 = cards.resolve_one("116N198V05"), cards.resolve_one("116N198V07")

    assert v5.ambiguous is False and v7.ambiguous is False
    assert v5.card["product_name"] == "Bajaj Life Superwoman Term"
    assert v7.card["product_name"] == "Bajaj Life eTouch II"
    assert (v5.card["structural_flags"]["offers_maturity_benefit"]
            != v7.card["structural_flags"]["offers_maturity_benefit"])


def test_an_ordinary_product_still_resolves_cleanly(cards):
    resolution = cards.resolve_one("116L215V01")

    assert resolution.ambiguous is False
    assert resolution.reason == "resolved"
    assert resolution.card["product_name"] == "Bajaj Life Smart Secure ROP"
    assert [c["uin"] for c in cards.lookup_many(["116L215V01"])] == ["116L215V01"]


def test_an_unknown_uin_resolves_to_nothing(cards):
    resolution = cards.resolve_one("116N999V01")

    assert (resolution.card, resolution.candidates, resolution.reason) == (
        None, [], "unknown")


def test_a_card_less_rider_yields_no_product_family(cards):
    """scripts/backfill_product_metadata.py claimed a rider brochure is 'rider'
    scope either way. It was not: it returned the PARENT's family — ulip for
    116N216V01, savings_endowment for 116B036V02."""
    from scripts.backfill_product_metadata import product_line_for_uin

    assert product_line_for_uin("116N216V01", cards) == "health", "has its OWN card now"
    assert product_line_for_uin("116B036V02", cards) is None
    assert product_line_for_uin("116A059V01", cards) == "rider", "has its OWN card"
    assert product_line_for_uin("116L215V01", cards) == "ulip"


# --------------------------------------------------------------------------
# C3 — the cache keys on knowledge content, not just identity.
# --------------------------------------------------------------------------


@pytest.fixture
def fingerprint():
    from app.config import settings
    from app.services.agents.compliance import analysis_cache as ac

    def _fp(*product_facts):
        return ac.run_context_fingerprint(settings, product_facts=list(product_facts))

    return _fp


def test_rewriting_a_guardrail_invalidates_the_cache(fingerprint, cards):
    card = dict(cards.get("116L215V01"))
    edited = dict(card, compliance_guardrails={
        "claims_marketing_must_avoid": ["A COMPLETELY DIFFERENT RULE"]})

    assert fingerprint(card) != fingerprint(edited)


def test_flipping_a_structural_flag_invalidates_the_cache(fingerprint, cards):
    card = dict(cards.get("116L215V01"))
    flags = dict(card["structural_flags"])
    flags["offers_guaranteed_benefits"] = not flags["offers_guaranteed_benefits"]

    assert fingerprint(card) != fingerprint(dict(card, structural_flags=flags))


def test_an_unchanged_card_keeps_the_cache(fingerprint, cards):
    card = cards.get("116L215V01")

    assert fingerprint(dict(card)) == fingerprint(dict(card))
    assert fingerprint(card) == fingerprint(json.loads(json.dumps(card)))


def test_editing_one_product_does_not_invalidate_another(fingerprint, cards):
    """Cross-product contamination is the failure mode a content hash could
    easily introduce. The hash is per-UIN, so it cannot."""
    a, b = dict(cards.get("116L215V01")), dict(cards.get("116N165V01"))
    before = fingerprint(a, b)
    edited_a = dict(a, compliance_guardrails={"claims_marketing_must_avoid": ["X"]})

    assert fingerprint(edited_a, b) != before
    assert fingerprint(b) == fingerprint(dict(cards.get("116N165V01")))


def test_a_filing_version_change_still_invalidates(fingerprint, cards):
    """V05 -> V07 is a different card AND a different UIN; both must move."""
    assert fingerprint(cards.get("116N198V05")) != fingerprint(cards.get("116N198V07"))


def test_the_fingerprint_is_stable_across_key_order(fingerprint, cards):
    card = dict(cards.get("116L215V01"))
    shuffled = {k: card[k] for k in reversed(list(card))}

    assert fingerprint(card) == fingerprint(shuffled)


# --------------------------------------------------------------------------
# C4 — the legacy precedent corpus declares its absence instead of omitting it.
# --------------------------------------------------------------------------


class _Hit:
    id = "x"
    score = 0.91
    fields = {"reviewer_name": "r", "comment_text": "c", "chunk_text": "t",
              "violation_category": "misleading_benefit", "severity": "high",
              "document_id": "d", "source_file": "f"}


def test_the_legacy_mapper_declares_the_missing_scope():
    from app.services.rag.retrievers.precedent_retriever import _hit_to_precedent_legacy

    mapped = _hit_to_precedent_legacy(_Hit())

    assert "product_category" in mapped, "declared absence, not a missing key"
    assert mapped["product_category"] is None


def test_the_legacy_corpus_is_still_honestly_rejected():
    """Making the key explicit must NOT make these usable. 5,229 rows from 431
    editorial articles carry no product scope; inventing one to raise the
    acceptance count would be fabricated grounding."""
    from app.services.rag.applicability import RetrievalScope, validate_precedents
    from app.services.rag.retrievers.precedent_retriever import _hit_to_precedent_legacy

    scope = RetrievalScope(uins=frozenset({"116L215V01"}),
                           categories=frozenset({"ulip"}), resolved=True,
                           declared_product_line="ulip")
    accepted, debug = validate_precedents(
        [dict(_hit_to_precedent_legacy(_Hit())) for _ in range(5)], scope)

    assert accepted == []
    assert debug[0]["verdict"] == "rejected"
    assert "scope_metadata_missing" in debug[0]["reason"]


def test_the_legacy_fallback_is_announced_once(caplog):
    import logging

    from app.services.rag.retrievers import precedent_retriever as pr

    pr._legacy_scope_warning_emitted = False
    with caplog.at_level(logging.WARNING):
        pr.warn_legacy_corpus_is_unscoped()
        pr.warn_legacy_corpus_is_unscoped()

    hits = [r for r in caplog.records if "product_category" in r.getMessage()]
    assert len(hits) == 1, "once per process, not once per chunk"
    assert "rag_compliance_examples" in hits[0].getMessage()


# --------------------------------------------------------------------------
# Partial-analysis compatibility: PR #20's contract must be untouched.
# --------------------------------------------------------------------------


def test_the_rider_gap_still_warns_rather_than_refusing(cards):
    from app.services.agents.compliance.engine import ComplianceEngine
    from app.services.product_resolver import unresolved_product_signals

    text = ("Bajaj Life Smart Secure ROP (UIN: 116L215V01) guarantees 12%. "
            "Bajaj Life Health Shield Rider (UIN:116B036V02).")
    signals = unresolved_product_signals(text, cards)

    assert signals["rider_uins_without_fact_cards"] == ["116B036V02"]
    assert ComplianceEngine.evaluate_persistability({
        "chunks": [{"id": "c1", "text": text}], "status": "completed",
        "metadata": {
            "product_unresolved": signals,
            "grounded_evidence": {"rules": 0, "precedents": 0, "product_facts": 1},
        },
    }) == (True, None)


def test_an_ambiguous_uin_still_fails_closed(cards):
    from app.services.agents.compliance.engine import ComplianceEngine
    from app.services.product_resolver import resolve_products
    from app.config import settings

    matches = resolve_products("Product UIN: 116L214V01 applies.", cards,
                               min_fuzzy_score=settings.kb_min_fuzzy_score)
    entry = next(m for m in matches if m["uin"] == "116L214V01")

    assert entry["ambiguous"] is True
    assert ComplianceEngine.evaluate_persistability({
        "chunks": [{"id": "c1", "text": "x"}], "status": "completed",
        "metadata": {"product_ambiguous_uins": ["116L214V01"],
                     "grounded_evidence": {"rules": 5, "precedents": 1,
                                           "product_facts": 0}},
    }) == (False, "product_ambiguous")
