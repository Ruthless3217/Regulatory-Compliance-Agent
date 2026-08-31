"""A product's fact card belongs in the chunk where its claims are analysed.

Grounding was per DOCUMENT while analysis is per CHUNK: the same up-to-three
cards went into all 27 chunk prompts. Measured with the real chunker on a real
5-product range brochure, that left 2 of 5 chunk-product pairs ungrounded — and
because `_findings_to_violations` discards a `product_fact_finding` whose
product_index is outside the grounded list, the sections about those two
products could not produce a product-specific finding at all.

Grounding each chunk with the products it names fixes that AND costs less: the
same brochure went 3/5 -> 5/5 pairs at 2,476 instead of 7,630 fact-card tokens
(-68%), because a card is no longer copied into chunks that never mention it.

Chunk-only grounding is NOT safe on its own. Measured on a document whose
claim-bearing section names no product ("The product guarantees returns of 12%
every year..."), it grounded that chunk with nothing, which is strictly worse
than today. The document-level selection is therefore kept as the fallback for
any chunk that names no product, which makes the model never worse than the
one it replaces on any measured axis.

Chunk detection SELECTS FROM the document's resolved products. It never
introduces identity of its own, so resolver precision, the identity-token gate,
ambiguity handling and regulatory scope are all untouched.
"""
from pathlib import Path

import pytest

from app.config import settings
from app.services.agents.graph.nodes import (
    _chunk_product_facts,
    _chunk_product_matches,
    _select_grounded_products,
)
from app.services.fact_card_service import FactCardService
from app.services.preprocessing_service import ContextEngineeringService
from app.services.product_resolver import resolve_products
from app.services.rag.applicability import build_scope

CARDS_DIR = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"
MIN_FUZZY = settings.kb_min_fuzzy_score
BUDGET = 3

ULIP1 = "116L196V04"     # Fortune Gain II     -> ulip
TERM = "116N165V01"      # Saral Jeevan Bima   -> term
PAR = "116N127V04"       # Elite Assure        -> par, savings_endowment
GROUP = "116G133V01"     # Pradhan Mantri JJBY -> group
PENSION = "116N169V16"   # Saral Pension       -> non_par, pension_annuity

FILL = ("This section describes the plan in detail for prospective policyholders "
        "and their advisers, covering the structure of the contract, the premium "
        "payment term and the servicing channels available after issuance. ") * 3


@pytest.fixture(scope="module")
def cards():
    service = FactCardService(CARDS_DIR)
    assert not service.availability_issues, service.availability_issues
    return service


@pytest.fixture(scope="module")
def name(cards):
    return {p["uin"]: p["product_name"] for p in cards.all_products()}


def _chunks(doc):
    """Real chunker output, plus the ids the DB assigns before graph state.

    `_chunk_text` returns {text, token_count, metadata}; `preprocess_node` reads
    persisted ContentChunk rows, so graph-state chunks always carry an `id`.
    """
    raw = ContextEngineeringService(db=None)._chunk_text(doc, "markdown")
    return [dict(c, id=f"chunk-{i}", chunk_index=i) for i, c in enumerate(raw)]


def _ground(doc, cards):
    """(chunks, per-chunk cards) exactly as dispatch_node builds them."""
    chunks = _chunks(doc)
    matches = resolve_products(doc, cards, min_fuzzy_score=MIN_FUZZY)
    document_grounded = _select_grounded_products(matches, doc, cards, BUDGET)
    by_chunk = _chunk_product_facts(
        chunks, matches, cards, document_grounded, BUDGET
    )
    return chunks, by_chunk, matches, document_grounded


def _uins(cards_for_chunk):
    return [c["uin"] for c in cards_for_chunk]


def _section(title, body):
    return f"## {title}\n\n{body} {FILL}"


def _range_brochure(name):
    bodies = [
        ("Disclosure", "{n} (UIN: {u}) guarantees returns of 12% every year with zero market risk."),
        ("Marketing", "{n} (UIN: {u}) is the cheapest plan in the market."),
        ("Surrender", "{n} (UIN: {u}) allows withdrawal anytime with no penalty."),
        ("Eligibility", "{n} (UIN: {u}) is open to all with instant approval."),
        ("Exclusions", "{n} (UIN: {u}) carries no exclusions."),
    ]
    order = [ULIP1, TERM, PAR, GROUP, PENSION]
    return "\n\n".join(
        _section(t, b.format(n=name[u], u=u)) for (t, b), u in zip(bodies, order)
    )


# --------------------------------------------------------------------------
# Each chunk is grounded with the products it is actually about.
# --------------------------------------------------------------------------


def test_single_product_document_grounds_that_product_everywhere(cards, name):
    doc = (_section("Plan", f"{name[ULIP1]} (UIN: {ULIP1}) guarantees 12% returns.")
           + "\n\n" + _section("Terms", "Terms and conditions apply."))
    chunks, by_chunk, _, _ = _ground(doc, cards)

    for chunk in chunks:
        assert _uins(by_chunk[str(chunk["id"])]) == [ULIP1]


def test_each_section_is_grounded_with_its_own_product(cards, name):
    """The measured gap: 2 of 5 sections had no card for the product they discuss."""
    doc = _range_brochure(name)
    chunks, by_chunk, _, document_grounded = _ground(doc, cards)
    expected = [ULIP1, TERM, PAR, GROUP, PENSION]

    assert len(chunks) == len(expected)
    for chunk, uin in zip(chunks, expected):
        assert _uins(by_chunk[str(chunk["id"])]) == [uin]
    # And the two that document-level grounding could never reach.
    assert GROUP not in document_grounded and PENSION not in document_grounded


def test_a_product_named_only_once_still_reaches_its_chunk(cards, name):
    doc = _range_brochure(name)
    _, by_chunk, _, _ = _ground(doc, cards)
    grounded_anywhere = {u for cs in by_chunk.values() for u in _uins(cs)}

    assert PENSION in grounded_anywhere


def test_a_later_product_is_not_starved_by_earlier_ones(cards, name):
    doc = _range_brochure(name)
    chunks, by_chunk, _, _ = _ground(doc, cards)

    assert _uins(by_chunk[str(chunks[-1]["id"])]) == [PENSION]


# --------------------------------------------------------------------------
# The boundary case that makes chunk-only grounding unsafe.
# --------------------------------------------------------------------------


def test_a_chunk_naming_no_product_falls_back_to_document_grounding(cards, name):
    """Claims live here, the product name does not. This chunk must not go bare."""
    doc = "\n\n".join([
        _section("About", f"{name[PENSION]} (UIN: {PENSION}) is our flagship annuity."),
        _section("Key benefits", "The product guarantees returns of 12% every year "
                                 "with zero risk and no penalty, and states no exclusions."),
        _section("Other plans", f"{name[ULIP1]} (UIN: {ULIP1})."),
    ])
    chunks, by_chunk, _, document_grounded = _ground(doc, cards)

    claims = next(c for c in chunks if "guarantees returns" in c["text"])
    assert _chunk_product_matches(claims["text"], []) == []
    assert _uins(by_chunk[str(claims["id"])]) == list(document_grounded)
    assert by_chunk[str(claims["id"])], "a claim-bearing chunk is never left bare"


def test_no_chunk_is_ever_grounded_with_less_than_document_level_would_give(cards, name):
    """The safety property: never worse than the model it replaces."""
    for doc in (_range_brochure(name),
                _section("All", f"{name[ULIP1]} (UIN: {ULIP1}) and {name[TERM]} "
                                f"(UIN: {TERM}) guarantee 12% returns.")):
        chunks, by_chunk, _, document_grounded = _ground(doc, cards)
        for chunk in chunks:
            grounded = _uins(by_chunk[str(chunk["id"])])
            named = {m["uin"] for m in _chunk_product_matches(
                chunk["text"], resolve_products(doc, cards, min_fuzzy_score=MIN_FUZZY))}
            # Either it covers what the chunk names, or it inherits the document's.
            assert (named <= set(grounded)) or grounded == list(document_grounded)


# --------------------------------------------------------------------------
# Budget, determinism and multi-product chunks.
# --------------------------------------------------------------------------


def test_a_chunk_naming_more_products_than_the_budget_stays_bounded(cards, name):
    doc = _section("Comparison", " ".join(
        f"{name[u]} (UIN: {u})," for u in [ULIP1, TERM, PAR, GROUP, PENSION]
    ) + " all guarantee 12% returns with no penalty.")
    chunks, by_chunk, _, _ = _ground(doc, cards)

    for chunk in chunks:
        assert len(by_chunk[str(chunk["id"])]) <= BUDGET


def test_a_multi_product_chunk_grounds_more_than_one_product(cards, name):
    doc = _section("Comparison", f"{name[ULIP1]} (UIN: {ULIP1}) and {name[TERM]} "
                                 f"(UIN: {TERM}) both guarantee 12% returns.")
    chunks, by_chunk, _, _ = _ground(doc, cards)

    assert set(_uins(by_chunk[str(chunks[0]["id"])])) == {ULIP1, TERM}


def test_chunk_grounding_is_deterministic(cards, name):
    doc = _range_brochure(name)
    runs = {
        tuple((cid, tuple(_uins(cards_for_chunk)))
              for cid, cards_for_chunk in sorted(_ground(doc, cards)[1].items()))
        for _ in range(5)
    }

    assert len(runs) == 1


def test_chunk_detection_never_invents_a_product(cards, name):
    """It selects from the document's resolved set; it cannot add identity."""
    doc = _range_brochure(name)
    _, by_chunk, matches, _ = _ground(doc, cards)
    resolved = {m["uin"] for m in matches}

    for chunk_cards in by_chunk.values():
        assert set(_uins(chunk_cards)) <= resolved


def test_generic_corporate_document_grounds_nothing_anywhere(cards):
    doc = "\n\n".join([
        _section("About us", "Bajaj Life Insurance is committed to helping "
                             "customers protect their financial future."),
        _section("Contact", "Contact Bajaj Life Insurance for more information."),
    ])
    chunks, by_chunk, matches, _ = _ground(doc, cards)

    assert matches == []
    for chunk in chunks:
        assert by_chunk[str(chunk["id"])] == []


# --------------------------------------------------------------------------
# Scope, cross-cutting evidence and audit.
# --------------------------------------------------------------------------


def test_regulatory_scope_is_unchanged_by_chunk_grounding(cards, name):
    """Grounding is contextual; scope stays document-level and complete."""
    doc = _range_brochure(name)
    _, _, matches, _ = _ground(doc, cards)
    scope = build_scope(matches, cards)

    assert {m["uin"] for m in matches} == {ULIP1, TERM, PAR, GROUP, PENSION}
    assert scope.categories >= {
        "ulip", "term", "par", "savings_endowment", "group",
        "non_par", "pension_annuity",
    }


def test_chunk_grounding_reduces_total_fact_card_volume(cards, name):
    """The cost argument: cards stop being copied into chunks that never name them."""
    doc = _range_brochure(name)
    chunks, by_chunk, _, document_grounded = _ground(doc, cards)

    chunk_aware = sum(len(by_chunk[str(c["id"])]) for c in chunks)
    document_level = len(document_grounded) * len(chunks)

    assert chunk_aware < document_level


def test_audit_can_reconstruct_the_product_to_chunk_mapping(cards, name):
    from app.services.agents.graph.nodes import _chunk_grounding_audit

    doc = _range_brochure(name)
    chunks, by_chunk, matches, document_grounded = _ground(doc, cards)
    audit = _chunk_grounding_audit(chunks, by_chunk, matches, document_grounded)

    assert audit["products_detected"] == len(matches)
    assert audit["chunks"] == len(chunks)
    assert set(audit["products_grounded"]) == {
        ULIP1, TERM, PAR, GROUP, PENSION,
    }
    assert audit["product_chunks"][PENSION] == [4]
    assert audit["chunks_using_document_fallback"] == []
