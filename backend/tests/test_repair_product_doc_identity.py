"""scripts/repair_product_doc_identity.py — the 860-vector identity repair.

Exercised against an in-memory model of `product_documents` and
`rag_product_docs` that implements the script's four SQL statements (two
SELECTs, two UPDATEs) with real rowcounts, real WHERE semantics and the
BEFORE UPDATE trigger's effect on search_tsv. Nothing here touches a database.

The properties under test are the ones a mistaken --apply would violate:
  * dry-run performs no write, and --apply is never implied
  * the repairable set is exactly the reviewed one, or the script stops
  * only `uin` changes on vectors; only `uin` changes on documents
  * embedding, text, chunk_index, ids, model, dim, product_name, search_tsv
    and updated_at are byte-identical before and after
  * a row that moved underneath the dry run aborts the whole transaction
  * class D and ambiguous documents are never touched
  * a second --apply is a no-op
"""
import copy
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import repair_product_doc_identity as repair  # noqa: E402


def _md5(s):
    return hashlib.md5(str(s).encode()).hexdigest()


# --------------------------------------------------------------------------
# An in-memory stand-in for the two tables, honouring the script's SQL.
# --------------------------------------------------------------------------


class _Result:
    def __init__(self, rows=None, rowcount=0):
        self._rows, self.rowcount = rows or [], rowcount

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class FakeSession:
    """Implements exactly the statements the script issues, nothing more."""

    def __init__(self, documents, vectors):
        self.documents = {d["id"]: dict(d) for d in documents}
        self.vectors = {v["id"]: dict(v) for v in vectors}
        self._snapshot = None
        self.committed = self.rolled_back = False
        self.statements = []

    # --- transaction semantics: writes are visible until rollback -------------
    def _begin(self):
        if self._snapshot is None:
            self._snapshot = (copy.deepcopy(self.documents), copy.deepcopy(self.vectors))

    def commit(self):
        self.committed, self._snapshot = True, None

    def rollback(self):
        self.rolled_back = True
        if self._snapshot:
            self.documents, self.vectors = self._snapshot
            self._snapshot = None

    def close(self):
        pass

    # --- the four statements ------------------------------------------------
    def execute(self, stmt, params=None):
        sql, params = str(stmt), params or {}
        self.statements.append((sql.strip().split()[0].upper(), params))
        self._begin()
        if "FROM product_documents pd" in sql:
            rows = []
            for d in self.documents.values():
                rows.append({**d, "vectors": sum(
                    1 for v in self.vectors.values() if v["product_document_id"] == d["id"])})
            return _Result(rows)
        if "md5(embedding::text)" in sql:
            ids = set(params["ids"])
            return _Result([{
                "id": v["id"], "doc_id": v["product_document_id"],
                "chunk_index": v["chunk_index"],
                "embedding_md5": _md5(v["embedding"]), "text_md5": _md5(v["text"]),
                "embedding_model": v["embedding_model"], "embedding_dim": v["embedding_dim"],
                "product_name": v["product_name"], "search_tsv_md5": _md5(v["search_tsv"]),
                "updated_at": v["updated_at"],
            } for v in self.vectors.values() if v["product_document_id"] in ids])
        if sql.lstrip().startswith("UPDATE rag_product_docs"):
            n = 0
            for v in self.vectors.values():
                if v["product_document_id"] == params["doc_id"] and v["uin"] == params["stored_uin"]:
                    v["uin"] = params["uin"]
                    # the BEFORE UPDATE trigger: search_tsv is rebuilt from
                    # text + section_path + product_name — all unchanged
                    v["search_tsv"] = _tsv(v)
                    n += 1
            return _Result(rowcount=n)
        if sql.lstrip().startswith("UPDATE product_documents"):
            d = self.documents.get(params["doc_id"])
            if d and d["uin"] == params["stored_uin"]:
                d["uin"] = params["uin"]
                return _Result(rowcount=1)
            return _Result(rowcount=0)
        raise AssertionError(f"unexpected SQL: {sql[:80]}")


def _tsv(v):
    return f"tsv({v['text']}|{v.get('section_path')}|{v['product_name']})"


def _doc(doc_id, name, uin, uins, status="ingested"):
    return {"id": doc_id, "product_name": name, "uin": uin, "uins": uins, "status": status}


def _vectors(doc_id, uin, name, n, start=0):
    out = []
    for i in range(n):
        v = {"id": f"{doc_id}-v{start + i}", "product_document_id": doc_id, "uin": uin,
             "product_name": name, "chunk_index": start + i, "text": f"chunk {i} of {name}",
             "section_path": "Benefits", "embedding": f"vec-{doc_id}-{i}",
             "embedding_model": "embed-multilingual-v3.0", "embedding_dim": 1024,
             "updated_at": "2026-06-16 21:15:10+00"}
        v["search_tsv"] = _tsv(v)
        out.append(v)
    return out


@pytest.fixture
def corpus():
    """A miniature of the real situation: two rider-stamped docs, one correct,
    one class D, one quarantined, plus 38-style orphans that no doc owns."""
    docs = [
        _doc("d-etouch", "eTouch II", "116B056V01",
             ["116B056V01", "116B058V01", "116N198V07"]),                 # SAFE
        _doc("d-fwg", "Future Wealth Gain IV", "116A057V02",
             ["116A057V02", "116A059V01", "116L202V01"]),                 # SAFE
        _doc("d-ss", "Bajaj Life Smart Secure ROP", "116L215V01", ["116L215V01"]),  # CORRECT
        _doc("d-isecure", "Isecure Insurance", "116B034V02",
             ["116B034V02", "116N208V03"]),                               # D: name matches no card
        _doc("d-q", "Gcpp", None, [], status="quarantined"),              # D
    ]
    vecs = (_vectors("d-etouch", "116B056V01", "eTouch II", 3)
            + _vectors("d-fwg", "116A057V02", "Future Wealth Gain IV", 2)
            + _vectors("d-ss", "116L215V01", "Bajaj Life Smart Secure ROP", 2)
            + _vectors("d-isecure", "116B034V02", "Isecure Insurance", 2)
            + _vectors("d-orphan", "116B034V02", "Bajaj Life ACE UIN", 1))
    return docs, vecs


@pytest.fixture
def expect_mini(monkeypatch):
    """The reviewed counts, scaled to the fixture (2 docs / 5 vectors)."""
    monkeypatch.setattr(repair, "EXPECTED_REPAIRABLE_DOCS", 2)
    monkeypatch.setattr(repair, "EXPECTED_REPAIRABLE_VECTORS", 5)


def _run(monkeypatch, session, argv):
    monkeypatch.setattr(repair, "SessionLocal", lambda: session)
    monkeypatch.setattr(sys, "argv", ["repair"] + argv)
    return repair.main()


# --------------------------------------------------------------------------
# Classification — the reviewed rule, reproduced.
# --------------------------------------------------------------------------


def test_classification_reproduces_the_reviewed_buckets(corpus):
    docs, vecs = corpus
    by_id = {d.doc_id: d for d in repair.classify(FakeSession(docs, vecs))}

    assert by_id["d-etouch"].verdict == repair.SAFE
    assert by_id["d-etouch"].proposed_uin == "116N198V07"
    assert by_id["d-fwg"].verdict == repair.SAFE
    assert by_id["d-fwg"].proposed_uin == "116L202V01"
    assert by_id["d-ss"].verdict == repair.CORRECT
    assert by_id["d-isecure"].verdict == repair.INSUFFICIENT
    assert by_id["d-q"].verdict == repair.INSUFFICIENT


def test_class_d_carries_no_proposed_uin(corpus):
    docs, vecs = corpus
    d = next(x for x in repair.classify(FakeSession(docs, vecs)) if x.doc_id == "d-isecure")

    assert d.proposed_uin is None, "116N208V03 is in uins[] but the name matches no card — not guessed"


def test_a_name_matching_two_plan_uins_is_ambiguous(corpus, monkeypatch):
    docs, vecs = corpus
    # Force S1 to return two plan UINs for the FWG document's name.
    monkeypatch.setattr(repair, "_match_name",
                        lambda name, by_name: {"116L202V01", "116L203V01"}
                        if "Future" in name else set())
    docs[1]["uins"] = ["116L202V01", "116L203V01"]
    d = next(x for x in repair.classify(FakeSession(docs, vecs)) if x.doc_id == "d-fwg")

    assert d.verdict == repair.AMBIGUOUS
    assert d.proposed_uin is None


# --------------------------------------------------------------------------
# Dry run — no writes, and --apply is never implied.
# --------------------------------------------------------------------------


def test_dry_run_is_the_default_and_writes_nothing(corpus, expect_mini, monkeypatch):
    docs, vecs = corpus
    session = FakeSession(docs, vecs)
    before = copy.deepcopy((session.documents, session.vectors))

    assert _run(monkeypatch, session, []) == 0
    assert (session.documents, session.vectors) == before
    assert not any(verb == "UPDATE" for verb, _ in session.statements)
    assert session.committed is False


def test_dry_run_reports_the_expected_plan(corpus, expect_mini, monkeypatch, caplog):
    docs, vecs = corpus
    _run(monkeypatch, FakeSession(docs, vecs), [])

    assert "REPAIRABLE: 2 documents / 5 vectors  (expected 2 / 5)" in caplog.text
    assert "DRY RUN" in caplog.text


# --------------------------------------------------------------------------
# Drift detection — the live corpus must match the reviewed classification.
# --------------------------------------------------------------------------


def test_refuses_on_unexpected_document_count(corpus, expect_mini, monkeypatch):
    docs, vecs = corpus
    docs.append(_doc("d-extra", "Goal Assure IV", "116A057V02",
                     ["116A057V02", "116L204V01"]))
    vecs += _vectors("d-extra", "116A057V02", "Goal Assure IV", 1)
    session = FakeSession(docs, vecs)

    assert _run(monkeypatch, session, ["--apply"]) == 2
    assert not any(verb == "UPDATE" for verb, _ in session.statements)


def test_refuses_on_unexpected_vector_count(corpus, expect_mini, monkeypatch):
    docs, vecs = corpus
    vecs += _vectors("d-etouch", "116B056V01", "eTouch II", 1, start=3)  # 6, not 5
    session = FakeSession(docs, vecs)

    assert _run(monkeypatch, session, ["--apply"]) == 2
    assert not any(verb == "UPDATE" for verb, _ in session.statements)


# --------------------------------------------------------------------------
# --apply — exactly the approved mutation, or nothing at all.
# --------------------------------------------------------------------------


def test_apply_changes_only_uin_on_vectors_and_documents(corpus, expect_mini, monkeypatch):
    docs, vecs = corpus
    session = FakeSession(docs, vecs)
    before = copy.deepcopy(session.vectors)

    assert _run(monkeypatch, session, ["--apply"]) == 0
    assert session.committed is True

    for vid, old in before.items():
        new = session.vectors[vid]
        if old["product_document_id"] in ("d-etouch", "d-fwg"):
            assert new["uin"] == {"d-etouch": "116N198V07", "d-fwg": "116L202V01"}[old["product_document_id"]]
        else:
            assert new["uin"] == old["uin"], "untouched documents keep their uin"
        for col in ("embedding", "text", "chunk_index", "id", "product_document_id",
                    "embedding_model", "embedding_dim", "product_name",
                    "search_tsv", "updated_at"):
            assert new[col] == old[col], f"{vid}.{col} must not change"
    assert session.documents["d-etouch"]["uin"] == "116N198V07"
    assert session.documents["d-fwg"]["uin"] == "116L202V01"
    assert session.documents["d-isecure"]["uin"] == "116B034V02", "class D untouched"
    assert session.documents["d-ss"]["uin"] == "116L215V01"


def test_apply_never_touches_class_d_or_orphans(corpus, expect_mini, monkeypatch):
    docs, vecs = corpus
    session = FakeSession(docs, vecs)
    _run(monkeypatch, session, ["--apply"])

    touched = {p["doc_id"] for verb, p in session.statements if verb == "UPDATE"}
    assert touched == {"d-etouch", "d-fwg"}
    assert all(session.vectors[v]["uin"] == "116B034V02"
               for v in session.vectors if v.startswith("d-orphan"))


def test_apply_refuses_when_a_vector_moved_underneath_the_dry_run(corpus, expect_mini, monkeypatch):
    """Guarded UPDATE: one vector's uin already changed -> rowcount short ->
    the whole transaction rolls back and nothing is committed."""
    docs, vecs = corpus
    session = FakeSession(docs, vecs)
    original = copy.deepcopy(session.vectors)

    real_execute = session.execute

    def _race(stmt, params=None):
        # Simulate a concurrent writer just before the first UPDATE.
        if str(stmt).lstrip().startswith("UPDATE rag_product_docs") and not getattr(session, "_raced", False):
            session._raced = True
            session.vectors["d-etouch-v1"]["uin"] = "SOMEONE-ELSE"
        return real_execute(stmt, params)

    session.execute = _race

    assert _run(monkeypatch, session, ["--apply"]) == 1
    assert session.rolled_back is True and session.committed is False
    for vid, v in session.vectors.items():
        if vid != "d-etouch-v1":
            assert v["uin"] == original[vid]["uin"], "partial writes were rolled back"


def test_apply_refuses_when_the_document_row_moved(corpus, expect_mini, monkeypatch):
    docs, vecs = corpus
    session = FakeSession(docs, vecs)
    real_execute = session.execute

    def _race(stmt, params=None):
        if str(stmt).lstrip().startswith("UPDATE product_documents") and not getattr(session, "_raced", False):
            session._raced = True
            session.documents[params["doc_id"]]["uin"] = "SOMEONE-ELSE"
        return real_execute(stmt, params)

    session.execute = _race

    assert _run(monkeypatch, session, ["--apply"]) == 1
    assert session.rolled_back is True


def test_apply_rolls_back_if_anything_but_uin_differs_afterwards(corpus, expect_mini, monkeypatch):
    """The post-update fingerprint is the last line of defence: if the trigger
    (or anything else) altered a protected column, nothing is committed."""
    docs, vecs = corpus
    session = FakeSession(docs, vecs)
    real_execute = session.execute

    def _corrupt(stmt, params=None):
        r = real_execute(stmt, params)
        if str(stmt).lstrip().startswith("UPDATE rag_product_docs"):
            session.vectors["d-fwg-v0"]["text"] = "REWRITTEN"
        return r

    session.execute = _corrupt

    assert _run(monkeypatch, session, ["--apply"]) == 1
    assert session.rolled_back is True and session.committed is False
    assert session.vectors["d-fwg-v0"]["text"] == "chunk 0 of Future Wealth Gain IV"


def test_apply_is_idempotent(corpus, expect_mini, monkeypatch):
    docs, vecs = corpus
    session = FakeSession(docs, vecs)
    assert _run(monkeypatch, session, ["--apply"]) == 0
    after_first = copy.deepcopy((session.documents, session.vectors))
    session.statements.clear()

    assert _run(monkeypatch, session, ["--apply"]) == 0, "a no-op, not a drift error"
    assert (session.documents, session.vectors) == after_first
    assert not any(verb == "UPDATE" for verb, _ in session.statements)


def test_apply_repairs_raises_on_a_non_safe_decision():
    d = repair.Decision("x", "n", "u", None, None, repair.INSUFFICIENT, "why", 1)

    with pytest.raises(RuntimeError, match="non-SAFE"):
        repair.apply_repairs(FakeSession([], []), [d])


def test_the_fingerprint_flags_any_protected_column():
    base = dict(row_id="r", doc_id="d", chunk_index=0, embedding_md5="e", text_md5="t",
                embedding_model="m", embedding_dim=1024, product_name="p",
                search_tsv_md5="s", updated_at="u")
    before = {"r": repair.RowFingerprint(**base)}
    for col in ("embedding_md5", "text_md5", "chunk_index", "product_name",
                "search_tsv_md5", "updated_at", "embedding_model", "embedding_dim"):
        changed = {**base, col: (99 if isinstance(base[col], int) else "X")}
        with pytest.raises(RuntimeError, match="beyond its uin"):
            repair._assert_identical(before, {"r": repair.RowFingerprint(**changed)})
    with pytest.raises(RuntimeError, match="row set changed"):
        repair._assert_identical(before, {})


def test_the_script_imports_nothing_that_can_ingest_or_embed():
    """Import lines and SQL only — the docstring is allowed to NAME the parser
    when explaining the defect it repairs."""
    import inspect

    imports = [ln.strip() for ln in inspect.getsource(repair).splitlines()
               if ln.strip().startswith(("import ", "from "))]
    for forbidden in ("brochure_parser", "ingest_product_brochures", "embedder",
                      "product_docs_indexer", "rag.factory", "rag.indexers"):
        assert not any(forbidden in ln for ln in imports), forbidden
    for verb in ("DELETE", "INSERT", "TRUNCATE", "DROP", "ALTER"):
        assert not any(getattr(v, "text", "").lstrip().upper().startswith(verb)
                       for v in (repair._UPDATE_VECTORS, repair._UPDATE_DOCUMENT)), verb
    assert repair._UPDATE_VECTORS.text.lstrip().startswith("UPDATE rag_product_docs")
    assert "SET uin = :uin" in repair._UPDATE_VECTORS.text
    assert "product_name" not in repair._UPDATE_VECTORS.text, "uin only — the tsv trigger"
