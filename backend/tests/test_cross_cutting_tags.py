"""Cross-cutting precedent tags — tags that are real but deliberately do NOT
scope to a product family.

'child' is the live case (259 of 2,440 production precedents). It is not a
product family: it is a VARIANT of a ULIP (Smart Wealth Goal child-wealth
brochure, UIN 116L214V01, product_category=ulip) and separately a RIDER
benefit (Family Protect Rider - Parental Care, 116B056V01,
product_category=rider) attached to term plans. Scoping child-benefit
precedents to one family would hide them from the other.

These already behaved as global, but only via the "unmappable tag" fallback —
correct by accident. These tests pin the behaviour so a future vocabulary fix
(or a naive SQL pushdown) cannot silently narrow retrieval.
"""
from app.services.rag.applicability import (
    RetrievalScope,
    is_cross_cutting,
    normalize_category,
    validate_precedents,
)


def _scope(*cats):
    return RetrievalScope(uins=frozenset({"116L214V01"}), categories=frozenset(cats), resolved=True)


def _precedents(*tags):
    return [{"id": f"p{i}", "product_category": t, "score": 0.9} for i, t in enumerate(tags)]


# --- the tag is recognised as cross-cutting ----------------------------------

def test_child_is_cross_cutting():
    assert is_cross_cutting("Child")
    assert is_cross_cutting("child")
    assert is_cross_cutting("  CHILD  ")


def test_real_product_families_are_not_cross_cutting():
    for tag in ("ULIP", "Term", "Pension", "Savings", "Par", "Non-Par"):
        assert not is_cross_cutting(tag), tag


def test_untagged_is_not_cross_cutting():
    # NULL requires curation via its own path; don't conflate the two.
    assert not is_cross_cutting(None)
    assert not is_cross_cutting("")


def test_child_does_not_normalize_to_a_product_category():
    # It must never enter a scope comparison as if it were a family.
    assert normalize_category("Child") is None


# --- retrieval behaviour ------------------------------------------------------

def test_child_precedent_survives_a_ulip_scope():
    accepted, debug = validate_precedents(_precedents("Child"), _scope("ulip"))
    assert len(accepted) == 1
    assert debug[0]["verdict"] == "accepted"


def test_child_precedent_survives_a_term_scope_too():
    # The whole point: the same precedent must reach both families.
    accepted, _ = validate_precedents(_precedents("Child"), _scope("term"))
    assert len(accepted) == 1


def test_child_is_accepted_for_the_stated_reason_not_as_a_parse_failure():
    _, debug = validate_precedents(_precedents("Child"), _scope("ulip"))
    reason = debug[0]["reason"]
    assert "cross_cutting" in reason
    assert "unmappable" not in reason, "reason must not claim the tag failed to parse"


def test_conflicting_family_is_still_rejected_alongside_child():
    # Guards against the fix accidentally widening everything to global.
    accepted, debug = validate_precedents(_precedents("Child", "Term"), _scope("ulip"))
    assert len(accepted) == 1
    verdicts = {d["scope_value"]: d["verdict"] for d in debug}
    assert verdicts["Child"] == "accepted"
    assert verdicts["Term"] == "rejected"


def test_matching_family_and_child_both_accepted():
    accepted, _ = validate_precedents(_precedents("ULIP", "Child"), _scope("ulip"))
    assert len(accepted) == 2


def test_untagged_fails_closed_until_explicitly_classified():
    accepted, debug = validate_precedents(_precedents(None), _scope("ulip"))
    assert accepted == []
    assert debug[0]["verdict"] == "rejected"
    assert "untagged" in debug[0]["reason"]
