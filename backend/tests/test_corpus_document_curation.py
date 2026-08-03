"""Per-document corpus curation.

`precedent_cases` stores the embedding as a column, so deleting a row IS the
vector delete — these pin that the SQL is scoped to the layer, that a miss is a
LookupError rather than a silent no-op, and that added rows are stamped into the
layer they were added to.
"""
import asyncio
import uuid

import pytest

from app.services import corpus_layer_service as svc


class _Result:
    def __init__(self, rowcount=0, rows=None):
        self.rowcount = rowcount
        self._rows = rows or []

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Db:
    """Records every statement so the tests can assert on scoping."""

    def __init__(self, rowcount=0, rows=None):
        self.calls = []
        self._rowcount = rowcount
        self._rows = rows or []
        self.commits = 0

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, params or {}))
        if sql.startswith("SELECT COALESCE(source_file"):
            return _Result(rows=self._rows)
        if sql.startswith("SELECT COUNT"):
            return _Result(rowcount=0, rows=[])
        return _Result(rowcount=self._rowcount)

    def commit(self):
        self.commits += 1


@pytest.fixture
def layer(monkeypatch):
    """A layer that exists, and a no-op count refresh."""
    monkeypatch.setattr(svc, "get_layer", lambda db, lid: object())
    monkeypatch.setattr(svc, "_refresh_count", lambda db, lid: 0)
    return uuid.uuid4()


def test_delete_document_is_scoped_to_the_layer(layer):
    db = _Db(rowcount=7)
    assert svc.delete_document(db, layer, "brochure-2019.pdf") == 7
    sql, params = db.calls[0]
    assert sql.startswith("DELETE FROM precedent_cases")
    # Both predicates matter: the same file name may exist in another layer.
    assert "source_layer_id" in sql and "source_file" in sql
    assert params["src"] == "brochure-2019.pdf"
    assert db.commits == 1


def test_delete_document_rejects_blank_source_file(layer):
    db = _Db(rowcount=99)
    with pytest.raises(svc.CorpusLayerError):
        svc.delete_document(db, layer, "   ")
    assert db.calls == [], "must not issue an unscoped DELETE"


def test_delete_document_that_matches_nothing_raises(layer):
    with pytest.raises(LookupError):
        svc.delete_document(_Db(rowcount=0), layer, "absent.pdf")


def test_delete_item_scopes_to_layer_and_id(layer):
    db = _Db(rowcount=1)
    svc.delete_item(db, layer, uuid.uuid4())
    sql, _ = db.calls[0]
    assert "source_layer_id" in sql and "id = CAST(:iid AS UUID)" in sql


def test_delete_item_missing_raises(layer):
    with pytest.raises(LookupError):
        svc.delete_item(_Db(rowcount=0), layer, uuid.uuid4())


def test_unknown_layer_raises_before_touching_rows(monkeypatch):
    monkeypatch.setattr(svc, "get_layer", lambda db, lid: None)
    db = _Db(rowcount=5)
    with pytest.raises(LookupError):
        svc.delete_document(db, uuid.uuid4(), "x.pdf")
    with pytest.raises(LookupError):
        svc.delete_item(db, uuid.uuid4(), uuid.uuid4())
    assert db.calls == []


def test_list_documents_groups_by_source_file(layer):
    class _Row(dict):
        pass

    rows = [
        _Row(source_file="a.pdf", precedent_count=12, last_updated=None),
        _Row(source_file="", precedent_count=3, last_updated=None),
    ]
    docs = svc.list_documents(_Db(rows=rows), layer)
    assert docs[0] == {"source_file": "a.pdf", "precedent_count": 12, "last_updated": None}
    # An empty source_file surfaces as None rather than a blank-looking row.
    assert docs[1]["source_file"] is None


def test_added_rows_are_embedded_then_stamped_into_the_layer(layer, monkeypatch):
    seen = {}

    async def fake_upsert(rows):
        seen["rows"] = list(rows)
        return len(seen["rows"])

    import app.services.rag.indexers.precedent_indexer as indexer
    monkeypatch.setattr(indexer, "upsert_precedents", fake_upsert)

    db = _Db(rowcount=2)
    result = asyncio.run(
        svc.add_precedents(db, layer, [{"canonical_hash": "h1"}, {"canonical_hash": "h2"}])
    )

    assert result == {"indexed": 2, "assigned": 2}
    # Rows without an id get one, so the follow-up stamp can target them.
    assert all(r.get("id") for r in seen["rows"])
    sql, params = db.calls[0]
    assert sql.startswith("UPDATE precedent_cases SET source_layer_id")
    assert params["ids"] == [str(r["id"]) for r in seen["rows"]]


def test_add_to_unknown_layer_embeds_nothing(monkeypatch):
    """Fail before embedding, or the rows are orphaned outside every layer."""
    monkeypatch.setattr(svc, "get_layer", lambda db, lid: None)
    called = False

    async def fake_upsert(rows):
        nonlocal called
        called = True
        return len(list(rows))

    import app.services.rag.indexers.precedent_indexer as indexer
    monkeypatch.setattr(indexer, "upsert_precedents", fake_upsert)

    with pytest.raises(LookupError):
        asyncio.run(svc.add_precedents(_Db(), uuid.uuid4(), [{"canonical_hash": "h"}]))
    assert called is False
