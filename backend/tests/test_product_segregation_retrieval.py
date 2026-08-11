"""Product segregation pushed down into retrieval SQL — both legs.

The applicability judge (rag/applicability.validate_*) already refuses
wrong-product candidates AFTER retrieval. That is too late: a ULIP precedent
that scores well on a term creative still occupies a recall-pool slot, so the
term-applicable precedent that would have been cited never gets retrieved at
all. These tests pin the coarse cut that happens in SQL instead:

  * every product-bearing corpus accepts a `product_line` filter (0036)
  * the predicate is built once and injected into BOTH the vector and the
    keyword leg — scoping one leg only would leak through the other
  * the value list is fail-open on NULL and on global/cross-cutting tags,
    because the judge, not the SQL, is the strict gate
  * it carries every spelling the taggers actually write ('ULIP' from the
    precedent ingest heuristic, 'ulip' from the rule/submission API) — a
    canonical-only IN list would match zero ingested precedents
"""
import asyncio
from typing import Any, Dict, List, Optional

import pytest

from app.services.precedent_ingestion import _PRODUCT_HINTS
from app.services.rag.applicability import (
    RetrievalScope,
    normalize_category,
    scope_filter_values,
)
from app.services.rag.ports import SearchHit
from app.services.rag.stores.pgvector_store import (
    _build_filter_clause,
    _keyword_leg_sql,
    _upsert_params,
    _vector_leg_sql,
)


def _clause(filters, index):
    params: Dict[str, Any] = {}
    return _build_filter_clause(index, filters, params), params


def _scope(*categories, resolved=True, declared=None):
    return RetrievalScope(
        uins=frozenset({"116N165V01"}) if resolved else frozenset(),
        categories=frozenset(categories),
        resolved=resolved,
        declared_product_line=declared,
    )


# --- the whitelist now covers every product-bearing corpus --------------------

@pytest.mark.parametrize("index", [
    "rag_rules", "rag_source_docs", "rag_chunks", "rag_product_docs",
])
def test_product_line_filter_allowed_on_every_rag_corpus(index):
    clause, params = _clause({"product_line": ["term", None]}, index)
    assert "product_line IN (" in clause
    assert "product_line IS NULL" in clause
    assert list(params.values()) == ["term"]


def test_precedent_corpus_uses_its_own_scope_column():
    clause, _ = _clause({"product_category": ["term", None]}, "precedent_cases")
    assert "product_category IN (" in clause
    # precedent_cases has no product_line column — filtering it would query a
    # column that does not exist.
    with pytest.raises(ValueError, match="not allowed"):
        _clause({"product_line": ["term"]}, "precedent_cases")


def test_legacy_examples_corpus_stays_unscoped():
    # rag_compliance_examples predates product tagging; the precedent retriever
    # deliberately sends no filter on that fallback path.
    with pytest.raises(ValueError, match="not allowed"):
        _clause({"product_line": ["term"]}, "rag_compliance_examples")


# --- both legs, one predicate -------------------------------------------------

def test_product_predicate_reaches_vector_and_keyword_legs():
    clause, _ = _clause({"product_line": ["term", None]}, "rag_rules")
    vec = _vector_leg_sql("rag_rules", clause)
    kw = _keyword_leg_sql("rag_rules", clause)
    assert "product_line IN (" in vec and "product_line IS NULL" in vec
    assert "product_line IN (" in kw and "product_line IS NULL" in kw
    # deterministic tiebreak added by earlier work must survive
    assert vec.rstrip().splitlines()[-2].strip().startswith("ORDER BY")
    assert ", id" in vec and ", id" in kw


# --- value-list construction --------------------------------------------------

def test_unresolved_scope_sends_no_filter_at_all():
    assert scope_filter_values(_scope(resolved=False)) is None


def test_value_list_always_admits_untagged_and_global():
    values = scope_filter_values(_scope("term"))
    assert None in values, "untagged rows must stay retrievable (C2/C7)"
    assert "global" in values
    assert "all_products" in values
    assert "child" in values, "cross-cutting tags are not product-scoping"


@pytest.mark.parametrize("category", [
    "term", "ulip", "rider", "group", "savings_endowment", "pension_annuity",
    "par", "non_par",
])
def test_global_rows_retrievable_for_every_product(category):
    values = scope_filter_values(_scope(category))
    assert "global" in values and None in values
    clause, params = _clause({"product_line": values}, "rag_rules")
    assert "OR product_line IS NULL" in clause
    assert "global" in params.values()


@pytest.mark.parametrize("label,_needles", _PRODUCT_HINTS)
def test_value_list_carries_every_precedent_ingest_spelling(label, _needles):
    """The ingest heuristic writes title/upper labels ('ULIP', 'Non-Par').
    Whatever family a label normalizes to, a scope for that family must match
    the raw label — otherwise the pushdown silently excludes the whole corpus.
    """
    canonical = normalize_category(label)
    if canonical is None:
        # 'Child' is cross-cutting: accepted for every scope, not one family.
        assert label.lower() in scope_filter_values(_scope("term"))
        return
    assert label in scope_filter_values(_scope(canonical))


def test_wrong_product_values_are_absent():
    values = scope_filter_values(_scope("term"))
    for foreign in ("ulip", "ULIP", "Pension", "pension_annuity", "Non-Par"):
        assert foreign not in values


def test_declared_global_scope_admits_only_global_and_untagged():
    # A product-neutral creative resolves via its declaration with no category.
    values = scope_filter_values(_scope(resolved=True, declared="global"))
    assert "global" in values and None in values
    assert "term" not in values and "ULIP" not in values


def test_wrong_product_rows_excluded_by_the_clause():
    values = scope_filter_values(_scope("term"))
    _, params = _clause({"product_category": values}, "precedent_cases")
    bound = set(params.values())
    assert {"term", "Term"} <= bound
    assert not ({"ulip", "ULIP", "Pension"} & bound)
    assert None not in bound, "NULL must never be bound as a parameter"


# --- retrievers pass the filter ----------------------------------------------

class _FakeEmbedder:
    model = "fake"
    dim = 3

    async def embed(self, texts, input_type=None):
        return [[0.1, 0.2, 0.3] for _ in texts]


class _FakeStore:
    def __init__(self, hits: Optional[List[SearchHit]] = None):
        self.calls: List[Dict[str, Any]] = []
        self._hits = hits or []

    async def hybrid_search(self, **kwargs):
        self.calls.append(kwargs)
        return list(self._hits)


def _patch(monkeypatch, module, store, embedder=None):
    monkeypatch.setattr(module, "get_vector_store", lambda: store)
    monkeypatch.setattr(module, "get_embedder", lambda: embedder or _FakeEmbedder())


def test_rules_retriever_pushes_product_scope(monkeypatch):
    from app.services.rag.retrievers import rules_retriever as mod
    store = _FakeStore()
    _patch(monkeypatch, mod, store)
    values = scope_filter_values(_scope("term"))
    asyncio.run(mod.RulesRetriever().retrieve_per_chunk(
        chunks=[{"id": "c1", "text": "guaranteed returns"}],
        categories=["regulatory"],
        top_k=3,
        product_scope=values,
    ))
    filters = store.calls[0]["filters"]
    assert filters["product_line"] == values
    assert filters["category"] == "regulatory" and filters["is_active"] is True


def test_rules_retriever_without_scope_sends_no_product_filter(monkeypatch):
    from app.services.rag.retrievers import rules_retriever as mod
    store = _FakeStore()
    _patch(monkeypatch, mod, store)
    asyncio.run(mod.RulesRetriever().retrieve_per_chunk(
        chunks=[{"id": "c1", "text": "x"}], categories=["regulatory"], top_k=3,
    ))
    assert "product_line" not in store.calls[0]["filters"]


def _precedent_hit(pid="p1", product_category="Term", ticket=None):
    return SearchHit(id=pid, score=0.9, fields={
        "reviewer_role": "reviewer",
        "reviewer_comment": "This wording overstates the guarantee and must change.",
        "span_context": "ctx",
        "highlighted_span": "guaranteed",
        "product_category": product_category,
        "ticket": ticket,
    })


def test_precedent_retriever_pushes_product_scope(monkeypatch):
    from app.services.rag.retrievers import precedent_retriever as mod
    store = _FakeStore([_precedent_hit()])
    _patch(monkeypatch, mod, store)
    monkeypatch.setattr(mod, "_check_precedent_cases_populated", lambda: True)
    values = scope_filter_values(_scope("term"))
    out = asyncio.run(mod.PrecedentRetriever().retrieve_per_chunk(
        chunks=[{"id": "c1", "text": "guaranteed returns"}], product_scope=values,
    ))
    assert store.calls[0]["index"] == "precedent_cases"
    assert store.calls[0]["filters"] == {"product_category": values}
    assert len(out["c1"]) == 1


def test_precedent_retriever_legacy_fallback_stays_unfiltered(monkeypatch):
    from app.services.rag.retrievers import precedent_retriever as mod
    store = _FakeStore()
    _patch(monkeypatch, mod, store)
    monkeypatch.setattr(mod, "_check_precedent_cases_populated", lambda: False)
    asyncio.run(mod.PrecedentRetriever().retrieve_per_chunk(
        chunks=[{"id": "c1", "text": "x"}], product_scope=scope_filter_values(_scope("term")),
    ))
    assert store.calls[0]["index"] == "rag_compliance_examples"
    assert store.calls[0]["filters"] is None


def test_precedent_retriever_excludes_the_current_submission(monkeypatch):
    from app.services.rag.retrievers import precedent_retriever as mod
    sub_id = "11111111-2222-3333-4444-555555555555"
    store = _FakeStore([
        _precedent_hit("own", ticket=sub_id),
        _precedent_hit("other", ticket="99999999-0000-0000-0000-000000000000"),
        _precedent_hit("untagged", ticket=None),
    ])
    _patch(monkeypatch, mod, store)
    monkeypatch.setattr(mod, "_check_precedent_cases_populated", lambda: True)
    out = asyncio.run(mod.PrecedentRetriever().retrieve_per_chunk(
        chunks=[{"id": "c1", "text": "x"}], exclude_document_id=sub_id,
    ))
    ids = {p["id"] for p in out["c1"]}
    assert "own" not in ids, "a submission would grade against its own feedback"
    assert {"other", "untagged"} <= ids


def test_reviewer_precedent_ticket_is_the_submission_id():
    """The leakage guard compares `document_id`, which the retriever maps from
    `precedent_cases.ticket`. rule_feedback writes str(submission_id) there, so
    passing state['submission_id'] through dispatch matches on type."""
    from app.services.rag.retrievers.precedent_retriever import _hit_to_precedent
    assert _hit_to_precedent(_precedent_hit(ticket="abc"))["document_id"] == "abc"

    import uuid
    from app.services import rule_feedback_service as rfs
    sub_id = uuid.uuid4()

    class _V:
        id = uuid.uuid4()
        category = "Regulatory"
        severity = "moderate"
        description = "d"
        current_text = "guaranteed returns"
        cited_anchor_text = None
        cited_section = None
        suggested_fix = "returns are not guaranteed"

    row = rfs._reviewer_precedent_row(
        _V(), "confirm", reason=None, explanation=None, final_text=None,
        product_category="term", submission_id=sub_id,
    )
    assert row["ticket"] == str(sub_id)


def test_product_docs_retriever_pushes_product_scope(monkeypatch):
    from app.services.rag.retrievers import product_docs_retriever as mod
    store = _FakeStore()
    _patch(monkeypatch, mod, store)
    values = scope_filter_values(_scope("term"))
    asyncio.run(mod.ProductDocsRetriever().retrieve(
        query="what are the charges", uin="116N165V01", product_scope=values,
    ))
    filters = store.calls[0]["filters"]
    assert filters["product_line"] == values and filters["uin"] == "116N165V01"


# --- indexers stamp the scope going forward ----------------------------------

class _CapturingStore:
    def __init__(self):
        self.docs: Dict[str, List[Any]] = {}

    async def upsert(self, index, docs):
        self.docs.setdefault(index, []).extend(docs)


def test_rules_indexer_stamps_product_line(monkeypatch):
    from app.models.rule import Rule
    from app.services.rag.indexers import rules_indexer as mod
    store = _CapturingStore()
    _patch(monkeypatch, mod, store)
    rule = Rule(
        id="33333333-3333-3333-3333-333333333333", category="regulatory",
        severity="high", is_active=True, rule_text="No guaranteed returns.",
        keywords=["guarantee"], product_line="ulip",
    )
    asyncio.run(mod.upsert_rules([rule]))
    assert store.docs["rag_rules"][0].fields["product_line"] == "ulip"


def test_source_docs_indexer_stamps_product_line(monkeypatch):
    from app.services.rag.indexers import source_docs_indexer as mod
    store = _CapturingStore()
    _patch(monkeypatch, mod, store)
    asyncio.run(mod.index_source_document(
        document_id="44444444-4444-4444-4444-444444444444",
        document_title="IRDAI ULIP circular", regulator="IRDAI",
        full_text="Some regulator text about unit linked products.",
        product_line="ulip",
    ))
    assert all(d.fields["product_line"] == "ulip" for d in store.docs["rag_source_docs"])


def test_source_evidence_quote_stamps_product_line(monkeypatch):
    from app.services.rag.indexers import source_docs_indexer as mod
    store = _CapturingStore()
    _patch(monkeypatch, mod, store)
    body = "Illustrations must not project guaranteed returns."
    asyncio.run(mod.index_source_evidence_quote(
        document_id="44444444-4444-4444-4444-444444444444",
        document_title="t", regulator="IRDAI", full_text=body,
        source_quote="must not project guaranteed returns",
        evidence_index=0, product_line="term",
    ))
    assert store.docs["rag_source_docs"][0].fields["product_line"] == "term"


def test_product_docs_indexer_stamps_product_line(monkeypatch):
    from app.services.brochure_parser import BrochureSection, ParsedBrochure
    from app.services.rag.indexers import product_docs_indexer as mod
    store = _CapturingStore()
    _patch(monkeypatch, mod, store)
    brochure = ParsedBrochure(
        source_file="b.pdf", page_count=1, body_font_size=9.0,
        product_name="Smart Wealth Goal", descriptor="A ULIP", uin="116L214V01",
        uins=["116L214V01"],
        sections=[BrochureSection(
            heading_path=["Charges"], text="t", chunk_text="Charges — t",
            page_start=1, block_type="prose", token_count=3,
        )],
    )
    asyncio.run(mod.index_product_document("55555555-5555-5555-5555-555555555555",
                                           brochure, product_line="ulip"))
    assert store.docs["rag_product_docs"][0].fields["product_line"] == "ulip"


class _FakeChunk:
    def __init__(self, cid, sub_id):
        self.id = cid
        self.submission_id = sub_id
        self.chunk_index = 0
        self.text = "some copy"
        self.chunk_metadata = {}


class _FakeQuery:
    def __init__(self, rows, scalar):
        self._rows, self._scalar = rows, scalar

    def filter(self, *a, **kw):
        return self

    def order_by(self, *a, **kw):
        return self

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _FakeDB:
    def __init__(self, chunks, product_line):
        self._chunks, self._product_line = chunks, product_line

    def query(self, entity):
        from app.models.content_chunk import ContentChunk
        if entity is ContentChunk:
            return _FakeQuery(self._chunks, None)
        return _FakeQuery([], self._product_line)


def test_chunks_indexer_stamps_submission_product_line(monkeypatch):
    from app.services.rag.indexers import chunks_indexer as mod
    store = _CapturingStore()
    _patch(monkeypatch, mod, store)
    sub_id = "66666666-6666-6666-6666-666666666666"
    db = _FakeDB([_FakeChunk("c1", sub_id)], "pension_annuity")
    asyncio.run(mod.upsert_chunks_for_submission(sub_id, db))
    assert store.docs["rag_chunks"][0].fields["product_line"] == "pension_annuity"


# --- the store actually binds what the indexers stamp -------------------------

@pytest.mark.parametrize("index,extra", [
    ("rag_rules", {}),
    ("rag_chunks", {"submission_id": "s1"}),
    ("rag_source_docs", {"document_id": "d1", "derived_rule_ids": []}),
    ("rag_product_docs", {"product_document_id": "p1"}),
])
def test_upsert_binds_product_line(index, extra, monkeypatch):
    from app.services.rag.ports import VectorDoc
    monkeypatch.setattr(
        "app.services.rag.stores.pgvector_store._active_embedder_identity",
        lambda: ("fake", 3),
    )
    doc = VectorDoc(id="77777777-7777-7777-7777-777777777777", embedding=[0.1, 0.2],
                    fields={"product_line": "non_par", **extra})
    assert _upsert_params(index, doc)["product_line"] == "non_par"


@pytest.mark.parametrize("index,extra", [
    ("rag_rules", {}),
    ("rag_chunks", {"submission_id": "s1"}),
    ("rag_source_docs", {"document_id": "d1", "derived_rule_ids": []}),
    ("rag_product_docs", {"product_document_id": "p1"}),
])
def test_upsert_leaves_untagged_rows_null(index, extra, monkeypatch):
    from app.services.rag.ports import VectorDoc
    monkeypatch.setattr(
        "app.services.rag.stores.pgvector_store._active_embedder_identity",
        lambda: ("fake", 3),
    )
    doc = VectorDoc(id="77777777-7777-7777-7777-777777777777", embedding=[0.1],
                    fields=extra)
    assert _upsert_params(index, doc)["product_line"] is None
