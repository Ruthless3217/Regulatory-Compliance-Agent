"""Approved-brochure passages must belong to the product the chunk is about.

`rag_product_docs` is strictly product-partitioned — the indexer stamps `uin`,
`product_name` and `product_line` from one brochure onto every vector — and the
prompt introduces the block as "APPROVED BROCHURE PASSAGES (ADVISORY — approved
wording for THIS product; compare the section's disclaimers/benefit wording
against these; divergence may be a finding)".

Retrieval was scoped with `uin=uins[0]` for every chunk. Measured with the
retriever replaced by a recorder (no embedding or DB call): in an N-product
document whose sections each discuss one product, N-1 of N chunks were given
another product's approved wording and none of their own — 1/2 at two products,
2/3 at three, 4/5 at five. The single-product control was correct.

A chunk now retrieves for the products it is about, reusing the fact-card
selection that chunk-aware grounding already computes, so the two grounding
channels agree and the analysis cache key already covers the choice. Retrieval
stays one call per chunk: a chunk is scoped to its strongest product, not to
every product it names.
"""
import asyncio
from pathlib import Path

import pytest

from app.config import settings
from app.services.agents.graph import nodes as graph_nodes
from app.services.fact_card_service import FactCardService
from app.services.preprocessing_service import ContextEngineeringService
from app.services.product_resolver import resolve_products

CARDS_DIR = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"
MIN_FUZZY = settings.kb_min_fuzzy_score

ULIP1 = "116L196V04"     # Fortune Gain II     -> ulip
TERM = "116N165V01"      # Saral Jeevan Bima   -> term
PAR = "116N127V04"       # Elite Assure        -> par, savings_endowment
GROUP = "116G133V01"     # Pradhan Mantri JJBY -> group
PENSION = "116N169V16"   # Saral Pension       -> non_par, pension_annuity

FILL = ("This section describes the plan in detail for prospective policyholders "
        "and their advisers, covering the contract structure and the premium "
        "payment term available at inception. ") * 3


@pytest.fixture(scope="module")
def cards():
    service = FactCardService(CARDS_DIR)
    assert not service.availability_issues, service.availability_issues
    return service


@pytest.fixture(scope="module")
def name(cards):
    return {p["uin"]: p["product_name"] for p in cards.all_products()}


class _Recorder:
    """Stands in for the product-docs retriever. Records the scoping UIN and
    returns a passage stamped with it, exactly as the real corpus would."""

    def __init__(self, names):
        self.calls = []
        self._names = names

    async def retrieve(self, *, query, uin=None, top_k=None, product_scope=None):
        self.calls.append({"uin": uin, "top_k": top_k})
        if uin is None:
            return []
        return [{
            "uin": uin, "product_name": self._names.get(uin, "?"),
            "text": "approved wording", "section_path": "Benefits", "page_number": 1,
        }]


def _document(name, uins):
    return "\n\n".join(
        f"## Section {i + 1}\n\n{name[u]} (UIN: {u}) guarantees returns of "
        f"12% every year with zero risk. {FILL}"
        for i, u in enumerate(uins)
    )


def _run(doc, cards, name, monkeypatch):
    """dispatch's grounding path with the retriever recorded, not called."""
    raw = ContextEngineeringService(db=None)._chunk_text(doc, "markdown")
    chunks = [dict(c, id=f"c{i}", chunk_index=i) for i, c in enumerate(raw)]
    matches = resolve_products(doc, cards, min_fuzzy_score=MIN_FUZZY)
    grounded = graph_nodes._select_grounded_products(
        matches, doc, cards, settings.product_match_max
    )
    recorder = _Recorder(name)

    from app.services import fact_card_service as fcs
    from app.services.rag.retrievers import product_docs_retriever as prm

    monkeypatch.setattr(settings, "product_grounding_enabled", True)
    monkeypatch.setattr(fcs, "get_fact_card_service", lambda: cards)
    monkeypatch.setattr(prm, "get_product_docs_retriever", lambda: recorder)

    state = {"metadata": {"product_match": matches,
                          "product_grounding_uins": grounded}}
    _facts, passages = asyncio.run(
        graph_nodes._resolve_product_grounding(state, chunks)
    )
    return chunks, passages, recorder, matches


def _about(chunk, matches):
    return {m["uin"] for m in graph_nodes._chunk_product_matches(chunk["text"], matches)}


# --------------------------------------------------------------------------
# The measured defect.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("uins", [
    pytest.param([ULIP1], id="1-product-control"),
    pytest.param([ULIP1, TERM], id="2-products"),
    pytest.param([ULIP1, TERM, PAR], id="3-products"),
    pytest.param([ULIP1, TERM, PAR, GROUP, PENSION], id="5-products"),
])
def test_no_chunk_is_given_another_products_approved_wording(
    uins, cards, name, monkeypatch
):
    doc = _document(name, uins)
    chunks, passages, _, matches = _run(doc, cards, name, monkeypatch)

    for chunk in chunks:
        about = _about(chunk, matches)
        got = {p["uin"] for p in passages.get(str(chunk["id"])) or []}
        assert not (got - about), (
            f"chunk about {sorted(about)} received wording for {sorted(got - about)}"
        )


@pytest.mark.parametrize("uins", [
    pytest.param([ULIP1, TERM], id="2-products"),
    pytest.param([ULIP1, TERM, PAR, GROUP, PENSION], id="5-products"),
])
def test_every_chunk_receives_its_own_products_wording(uins, cards, name, monkeypatch):
    """The more serious half: the correct evidence used to be absent."""
    doc = _document(name, uins)
    chunks, passages, _, matches = _run(doc, cards, name, monkeypatch)

    for chunk in chunks:
        about = _about(chunk, matches)
        if not about:
            continue
        got = {p["uin"] for p in passages.get(str(chunk["id"])) or []}
        assert got & about, f"chunk about {sorted(about)} got no wording of its own"


def test_single_product_document_is_unchanged(cards, name, monkeypatch):
    """The control: this case was already correct and must stay correct."""
    doc = _document(name, [ULIP1])
    chunks, passages, recorder, _ = _run(doc, cards, name, monkeypatch)

    assert {c["uin"] for c in recorder.calls} == {ULIP1}
    for chunk in chunks:
        assert [p["uin"] for p in passages[str(chunk["id"])]] == [ULIP1]


# --------------------------------------------------------------------------
# Bounded cost, determinism and the fallbacks.
# --------------------------------------------------------------------------


def test_retrieval_stays_one_call_per_chunk(cards, name, monkeypatch):
    """A correctness fix must not turn into 5 products x N chunks of retrieval."""
    doc = _document(name, [ULIP1, TERM, PAR, GROUP, PENSION])
    chunks, _, recorder, _ = _run(doc, cards, name, monkeypatch)

    assert len(recorder.calls) == len(chunks)


def test_chunk_naming_no_product_falls_back_to_the_document_product(
    cards, name, monkeypatch
):
    """Claims here, product name elsewhere — evidence must not vanish."""
    doc = "\n\n".join([
        f"## About\n\n{name[PENSION]} (UIN: {PENSION}) is our annuity. {FILL}",
        f"## Key benefits\n\nThe product guarantees returns of 12% every year "
        f"with zero risk. {FILL}",
        f"## Other\n\n{name[ULIP1]} (UIN: {ULIP1}). {FILL}",
    ])
    chunks, passages, _, matches = _run(doc, cards, name, monkeypatch)

    claims = next(c for c in chunks if "guarantees returns" in c["text"])
    assert _about(claims, matches) == set()
    assert passages[str(claims["id"])], "a claim-bearing chunk keeps its evidence"


def test_a_multi_product_chunk_is_scoped_to_one_of_its_own_products(
    cards, name, monkeypatch
):
    """Bounded: one retrieval call, scoped inside the chunk's own products."""
    doc = (f"## Comparison\n\n{name[ULIP1]} (UIN: {ULIP1}) and {name[TERM]} "
           f"(UIN: {TERM}) both guarantee 12% returns. {FILL}")
    chunks, passages, recorder, matches = _run(doc, cards, name, monkeypatch)

    assert len(recorder.calls) == len(chunks)
    got = {p["uin"] for p in passages[str(chunks[0]["id"])]}
    assert got and got <= _about(chunks[0], matches)


def test_passage_scoping_is_deterministic(cards, name, monkeypatch):
    doc = _document(name, [ULIP1, TERM, PAR, GROUP, PENSION])
    results = set()
    for _ in range(5):
        chunks, passages, _, _ = _run(doc, cards, name, monkeypatch)
        results.add(tuple(
            (str(c["id"]), tuple(p["uin"] for p in passages[str(c["id"])]))
            for c in chunks
        ))

    assert len(results) == 1


def test_generic_document_retrieves_no_product_evidence(cards, name, monkeypatch):
    doc = (f"## About us\n\nBajaj Life Insurance is committed to helping "
           f"customers protect their financial future. {FILL}")
    chunks, passages, recorder, matches = _run(doc, cards, name, monkeypatch)

    assert matches == []
    assert recorder.calls == []
    for chunk in chunks:
        assert passages.get(str(chunk["id"]), []) == []


def test_passage_and_fact_card_grounding_agree_on_the_chunk(cards, name, monkeypatch):
    """The two product channels must not disagree about what a chunk is about."""
    doc = _document(name, [ULIP1, TERM, PAR, GROUP, PENSION])
    chunks, passages, _, matches = _run(doc, cards, name, monkeypatch)
    grounded = graph_nodes._select_grounded_products(
        matches, doc, cards, settings.product_match_max
    )
    by_chunk = graph_nodes._chunk_product_facts(
        chunks, matches, cards, grounded, settings.product_match_max
    )

    for chunk in chunks:
        cid = str(chunk["id"])
        passage_uins = {p["uin"] for p in passages.get(cid) or []}
        card_uins = {c["uin"] for c in by_chunk.get(cid) or []}
        assert passage_uins <= card_uins, (
            f"chunk {cid}: passages {sorted(passage_uins)} outside its cards "
            f"{sorted(card_uins)}"
        )
