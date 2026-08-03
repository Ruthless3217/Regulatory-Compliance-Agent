"""Corpus layers (migration 0030) — provenance + an off switch for the corpus.

Two things are load-bearing and both are pinned here:

1. **Disabled layers must vanish from retrieval, NULL layers must not.** Every
   precedent that predates 0030 (~2,440 in production) carries
   ``source_layer_id IS NULL``. A guard written as a naive
   ``source_layer_id NOT IN (...)`` would evaluate to NULL for those rows and
   silently drop the ENTIRE existing corpus — retrieval would return nothing and
   the analyzer would fail closed on knowledge_base_empty. The guard SQL is
   therefore EXECUTED here (against stdlib sqlite, which shares Postgres' NULL
   semantics for IN/NOT IN/IS NULL) rather than string-matched.

2. **Disabling is not deleting.** ``update_layer(enabled=False)`` must issue no
   DELETE at all, and ``delete_layer(purge=False)`` must leave the precedents
   alive.
"""
import sqlite3
import uuid

import pytest

from app.models.corpus_layer import CORPUS_LAYER_KINDS, CorpusLayer
from app.services import corpus_layer_service as svc
from app.services.rag.stores.pgvector_store import (
    _LAYER_GUARD,
    _RETURN_COLUMNS,
    _build_filter_clause,
    _keyword_leg_sql,
    _vector_leg_sql,
)


# =============================================================== the guard ===
# Mirrors what PgVectorStore.hybrid_search composes for a query.
def _clause(filters=None, index="precedent_cases"):
    params: dict = {}
    fclause = _build_filter_clause(index, filters, params) + _LAYER_GUARD.get(index, "")
    return fclause, params


def _db() -> sqlite3.Connection:
    """A miniature precedent_cases + corpus_layers, populated to cover every
    layer state a real row can be in."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE corpus_layers (id TEXT PRIMARY KEY, enabled BOOLEAN)")
    con.execute(
        "CREATE TABLE precedent_cases (id TEXT PRIMARY KEY, source_layer_id TEXT, "
        "product_category TEXT)"
    )
    con.executemany(
        "INSERT INTO corpus_layers VALUES (?, ?)",
        [("layer-on", 1), ("layer-off", 0)],
    )
    con.executemany(
        "INSERT INTO precedent_cases VALUES (?, ?, ?)",
        [
            ("legacy", None, None),          # pre-0030: no layer, no product tag
            ("legacy-ulip", None, "ulip"),   # pre-0030, tagged
            ("on", "layer-on", "ulip"),      # enabled layer
            ("off", "layer-off", "ulip"),    # DISABLED layer
        ],
    )
    return con


def _ids(filters=None) -> set:
    fclause, params = _clause(filters)
    con = _db()
    rows = con.execute(f"SELECT id FROM precedent_cases WHERE TRUE {fclause}", params)
    return {r[0] for r in rows}


# --- the reason this test file exists ----------------------------------------

def test_null_layer_rows_survive_the_guard():
    # The whole pre-0030 corpus has source_layer_id IS NULL. If this set is
    # missing them, production retrieval returns nothing.
    assert {"legacy", "legacy-ulip"} <= _ids()


def test_disabled_layer_rows_are_not_retrieved():
    assert "off" not in _ids()


def test_enabled_layer_rows_are_retrieved():
    assert "on" in _ids()


def test_guard_alone_returns_everything_except_the_disabled_layer():
    assert _ids() == {"legacy", "legacy-ulip", "on"}


def test_re_enabling_restores_rows_without_touching_embeddings():
    # Disabling is a boolean flip on corpus_layers, nothing else. Flip it back
    # and the row returns — no re-ingest, no re-embed.
    fclause, params = _clause()
    con = _db()
    con.execute("UPDATE corpus_layers SET enabled = 1 WHERE id = 'layer-off'")
    rows = con.execute(f"SELECT id FROM precedent_cases WHERE TRUE {fclause}", params)
    assert "off" in {r[0] for r in rows}


# --- composed with the existing product-scope filter --------------------------

def test_guard_composes_with_product_scope_and_keeps_untagged_candidates():
    # SQL retains untagged candidates for the Python applicability audit.
    # Adding the layer guard must not break that, and must still drop 'off'.
    assert _ids({"product_category": ["ulip", None]}) == {"legacy", "legacy-ulip", "on"}


def test_guard_composes_with_a_strict_product_scope():
    assert _ids({"product_category": ["ulip"]}) == {"legacy-ulip", "on"}


# --- wiring: the guard cannot be bypassed by a caller -------------------------

def test_both_search_legs_carry_the_guard():
    fclause, _ = _clause()
    assert "corpus_layers" in _vector_leg_sql("precedent_cases", fclause)
    assert "corpus_layers" in _keyword_leg_sql("precedent_cases", fclause)


def test_guard_applies_only_to_precedent_cases():
    # rag_rules et al. have no source_layer_id column; emitting the guard there
    # would be a query against a nonexistent column.
    for index in ("rag_rules", "rag_chunks", "rag_source_docs", "rag_product_docs"):
        assert _LAYER_GUARD.get(index, "") == ""


def test_guard_is_appended_even_when_the_caller_passes_no_filters():
    fclause, _ = _clause(None)
    assert "corpus_layers" in fclause


def test_source_layer_id_is_returned_so_provenance_is_visible():
    assert "source_layer_id" in _RETURN_COLUMNS["precedent_cases"]


def test_source_layer_id_is_not_a_caller_settable_filter():
    # The guard is mandatory, not opt-in — no caller gets to override it.
    from app.services.rag.stores.pgvector_store import _FILTER_WHITELIST
    assert "source_layer_id" not in _FILTER_WHITELIST["precedent_cases"]


# ============================================================== the service ===

class _Result:
    def __init__(self, scalar=0, rowcount=0, rows=()):
        self._scalar, self.rowcount, self._rows = scalar, rowcount, list(rows)

    def scalar(self):
        return self._scalar

    def all(self):
        return self._rows


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_):
        return self

    def order_by(self, *_):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class FakeSession:
    """Records the SQL the service issues. The contract under test is *which*
    statements run (a purge must DELETE, a disable must not), so the fake
    reports statements rather than pretending to be Postgres."""

    def __init__(self, existing=None, count=0):
        self.executed = []          # (sql, params)
        self.layers = list(existing or [])
        self.added, self.deleted = [], []
        self.commits = 0
        self.count = count

    # -- SQLAlchemy surface the service uses --
    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.executed.append((sql, params or {}))
        if sql.upper().startswith("SELECT COUNT"):
            return _Result(scalar=self.count)
        if sql.upper().startswith("SELECT SOURCE_LAYER_ID"):
            return _Result(rows=[])
        if sql.upper().startswith("SELECT "):
            return _Result(rows=[])
        return _Result(rowcount=self.count)

    def query(self, _model):
        return _Query(self.layers)

    def get(self, _model, pk):
        return next((l for l in self.layers if str(l.id) == str(pk)), None)

    def add(self, obj):
        self.added.append(obj)
        self.layers.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)
        if obj in self.layers:
            self.layers.remove(obj)

    def flush(self):
        for obj in self.layers:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    def commit(self):
        self.commits += 1

    def refresh(self, _obj):
        pass

    # -- assertions helper --
    def sql_matching(self, needle):
        return [s for s, _ in self.executed if needle.upper() in s.upper()]


def _layer(**kw):
    l = CorpusLayer(
        id=kw.pop("id", uuid.uuid4()),
        name=kw.pop("name", "batch-a"),
        kind=kw.pop("kind", "precedent_ingest"),
        enabled=kw.pop("enabled", True),
        **kw,
    )
    return l


# --- validation at the trust boundary ----------------------------------------

def test_unknown_kind_is_rejected():
    with pytest.raises(svc.CorpusLayerError, match="Unknown kind"):
        svc.create_layer(FakeSession(), name="x", kind="whatever")


def test_every_documented_kind_is_accepted():
    assert CORPUS_LAYER_KINDS == {
        "precedent_ingest", "reviewer_feedback", "source_docs", "product_docs"
    }
    for kind in CORPUS_LAYER_KINDS:
        svc.create_layer(FakeSession(), name=f"l-{kind}", kind=kind)


def test_blank_name_is_rejected():
    with pytest.raises(svc.CorpusLayerError, match="name is required"):
        svc.create_layer(FakeSession(), name="   ", kind="source_docs")


def test_duplicate_name_is_rejected():
    db = FakeSession(existing=[_layer(name="batch-a")])
    with pytest.raises(svc.CorpusLayerError, match="already exists"):
        svc.create_layer(db, name="batch-a", kind="precedent_ingest")


def test_garbage_layer_id_is_a_miss_not_a_crash():
    assert svc.get_layer(FakeSession(), "not-a-uuid") is None


# --- create + claim -----------------------------------------------------------

def test_creating_a_layer_without_source_ref_claims_nothing():
    db = FakeSession()
    svc.create_layer(db, name="empty", kind="reviewer_feedback")
    assert db.sql_matching("UPDATE precedent_cases") == []


def test_source_ref_adopts_only_unclaimed_rows():
    db = FakeSession(count=7)
    out = svc.create_layer(
        db, name="batch-a", kind="precedent_ingest", source_ref="dataset_2.1_rl/a.json"
    )
    updates = db.sql_matching("UPDATE precedent_cases")
    assert len(updates) == 1
    # Rows already owned by another layer must never be stolen.
    assert "source_layer_id IS NULL" in updates[0]
    assert out["item_count"] == 7


def test_claim_can_be_turned_off():
    db = FakeSession(count=7)
    svc.create_layer(
        db, name="batch-a", kind="precedent_ingest", source_ref="a.json", claim=False
    )
    assert db.sql_matching("UPDATE precedent_cases") == []


# --- disable is reversible and destroys nothing -------------------------------

def test_disabling_issues_no_delete():
    layer = _layer(enabled=True)
    db = FakeSession(existing=[layer])
    out = svc.update_layer(db, layer.id, enabled=False)
    assert out["enabled"] is False
    assert db.sql_matching("DELETE") == [], "disable must never destroy corpus content"


def test_enabling_is_the_same_cheap_flip_back():
    layer = _layer(enabled=False)
    db = FakeSession(existing=[layer])
    assert svc.update_layer(db, layer.id, enabled=True)["enabled"] is True
    assert db.sql_matching("DELETE") == []


def test_update_on_missing_layer_raises_lookup():
    with pytest.raises(LookupError):
        svc.update_layer(FakeSession(), uuid.uuid4(), enabled=False)


# --- delete / purge -----------------------------------------------------------

def test_purge_deletes_the_layers_precedent_rows():
    layer = _layer()
    db = FakeSession(existing=[layer], count=42)
    assert svc.purge_layer(db, layer.id) == 42
    deletes = db.sql_matching("DELETE FROM precedent_cases")
    assert len(deletes) == 1
    assert "source_layer_id" in deletes[0], "a purge must be scoped to its layer"


def test_delete_without_purge_leaves_precedents_alive():
    layer = _layer()
    db = FakeSession(existing=[layer], count=42)
    out = svc.delete_layer(db, layer.id, purge=False)
    assert db.sql_matching("DELETE FROM precedent_cases") == []
    assert out["precedents_deleted"] == 0
    assert out["precedents_orphaned"] == 42
    assert layer in db.deleted


def test_delete_with_purge_reports_what_it_destroyed():
    layer = _layer()
    db = FakeSession(existing=[layer], count=42)
    out = svc.delete_layer(db, layer.id, purge=True)
    assert out["purged"] is True
    assert out["precedents_deleted"] == 42
    assert out["precedents_orphaned"] == 0


def test_purge_is_logged_with_its_row_count(caplog):
    layer = _layer(name="bad-ingest")
    db = FakeSession(existing=[layer], count=42)
    with caplog.at_level("WARNING"):
        svc.purge_layer(db, layer.id)
    logged = [r.getMessage() for r in caplog.records]
    assert any("PURGED" in m and "42 precedent rows deleted" in m for m in logged)


def test_purge_on_missing_layer_raises_lookup():
    with pytest.raises(LookupError):
        svc.purge_layer(FakeSession(), uuid.uuid4())
