"""Reviewer feedback as a REMOVABLE LAYER over the precedents.

A verdict on a finding is a precedent in disguise: "this wording is wrong, and
here is the approved rewrite" / "this flag was wrong". Until now `apply_action`
only moved a rule's pseudo-counts, so the reviewer's judgment never reached the
corpus the next analysis retrieves from.

Two properties are load-bearing and both are pinned here:

1. **One violation, one precedent row.** The PK is derived from the violation
   id, so changing your mind rewrites the row (ON CONFLICT (id)) instead of
   teaching the corpus twice. A dismiss deletes it again.
2. **Feedback capture never depends on the vector store.** The RuleFeedback row
   is committed before this runs; an embedder outage must degrade to a log
   line, never to a lost verdict.

Removability itself is already pinned by test_corpus_layers.py (the
`_LAYER_GUARD` tests) — this file only proves the rows land in a layer that
guard can switch off.
"""
import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.api.routes.compliance import (
    REASON_TO_QUEUE,
    normalize_reason,
    resolve_routed_queue,
)
from app.models.corpus_layer import CorpusLayer
from app.models.violation import Violation
from app.services import rule_feedback_service as rfs


# ============================================================ reason routing ===
# The UI (ViolationCard) sends kebab-case reason values; the queue map was
# written in snake_case, so needs_severity_review and needs_legal_review were
# unreachable from every real click.

def test_hyphenated_ui_reasons_reach_their_queue():
    assert resolve_routed_queue("wrong-severity") == "needs_severity_review"
    assert resolve_routed_queue("needs-human-legal-review") == "needs_legal_review"
    assert resolve_routed_queue("valid-regulatory-exception") == "needs_legal_review"
    assert resolve_routed_queue("outdated-rule") == "needs_legal_review"
    assert resolve_routed_queue("duplicate") == "needs_dedup_review"


def test_snake_case_reasons_still_route_unchanged():
    # Rows written before the UI vocabulary was covered must keep routing.
    assert resolve_routed_queue("wrong_severity") == "needs_severity_review"
    assert resolve_routed_queue("out_of_scope") == "needs_legal_review"


def test_both_previously_unreachable_queues_are_now_reachable():
    assert {"needs_severity_review", "needs_legal_review"} <= set(REASON_TO_QUEUE.values())


def test_queue_map_keys_are_all_normalized():
    # A hyphenated key here would be dead on arrival: lookups are normalized.
    assert all(k == normalize_reason(k) for k in REASON_TO_QUEUE)


def test_normalize_reason_handles_case_padding_and_emptiness():
    assert normalize_reason("  Wrong-Severity ") == "wrong_severity"
    assert normalize_reason(None) is None
    assert normalize_reason("") is None
    assert normalize_reason("   ") is None


def test_ui_reasons_without_an_escalation_route_nowhere():
    # Model-quality signals, already captured on the feedback row itself.
    for reason in (
        "vague", "low-value", "insufficient-evidence", "unsupported-format",
        "wrong-product", "wrong-section", "wrong-context", "retrieval-mismatch",
        "hallucination", "other",
    ):
        assert resolve_routed_queue(reason) is None


# ================================================================ fake session ===

class _Result:
    def __init__(self, scalar=0, rowcount=0):
        self._scalar, self.rowcount = scalar, rowcount

    def scalar(self):
        return self._scalar

    def all(self):
        return []


class _Query:
    def __init__(self, rows):
        self._rows, self._preds = list(rows), []

    def filter(self, *exprs):
        for e in exprs:
            self._preds.append((e.left.key, e.right.value))
        return self

    def order_by(self, *_):
        return self

    def first(self):
        for obj in self._rows:
            if all(getattr(obj, k, None) == v for k, v in self._preds):
                return obj
        return None

    def all(self):
        return list(self._rows)


class FakeSession:
    """Reports the SQL the service issues (the contract is *which* statements
    run), and serves the violation + layer lookups from memory."""

    def __init__(self, violation=None, layers=None, rowcount=1, count=1):
        self.violation = violation
        self.layers = list(layers or [])
        self.executed = []
        self.commits = self.rollbacks = 0
        self.rowcount, self.count = rowcount, count

    def query(self, model):
        if model is CorpusLayer:
            return _Query(self.layers)
        return _Query([self.violation] if self.violation is not None else [])

    def get(self, _model, pk):
        return next((l for l in self.layers if str(l.id) == str(pk)), None)

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.executed.append((sql, params or {}))
        if sql.upper().startswith("SELECT COUNT"):
            return _Result(scalar=self.count)
        return _Result(rowcount=self.rowcount)

    def add(self, obj):
        self.layers.append(obj)

    def delete(self, obj):
        if obj in self.layers:
            self.layers.remove(obj)

    def flush(self):
        for obj in self.layers:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, _obj):
        pass

    def sql_matching(self, needle):
        return [(s, p) for s, p in self.executed if needle.upper() in s.upper()]


def _violation(**kw):
    check = SimpleNamespace(
        submission_id=kw.pop("submission_id", uuid.uuid4()),
        submission=SimpleNamespace(product_line=kw.pop("product_line", "ULIP")),
    )
    base = dict(
        id=uuid.uuid4(),
        category="disclosure",
        severity="high",
        description="Return figures are shown without the mandated disclaimer.",
        current_text="Guaranteed 12% returns every year.",
        suggested_fix="Illustrative returns; not guaranteed.",
        cited_anchor_text=None,
        cited_section="IRDAI Adv. Guidelines §5(1)",
        compliance_check=check,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _layer(name=None, **kw):
    return CorpusLayer(
        id=kw.pop("id", uuid.uuid4()),
        name=name or rfs.REVIEWER_FEEDBACK_LAYER_NAME,
        kind=kw.pop("kind", "reviewer_feedback"),
        enabled=kw.pop("enabled", True),
        **kw,
    )


@pytest.fixture
def captured(monkeypatch):
    """Capture what would have been embedded, without an embedder."""
    seen = []

    async def fake_upsert(rows):
        rows = list(rows)
        seen.extend(rows)
        return len(rows)

    import app.services.rag.indexers.precedent_indexer as indexer
    monkeypatch.setattr(indexer, "upsert_precedents", fake_upsert)
    return seen


def _sync(db, violation_id, action, **kw):
    return asyncio.run(rfs.sync_reviewer_precedent(db, violation_id, action, **kw))


# ======================================================== the precedent write ===

def test_correct_verdict_writes_a_precedent_into_the_reviewer_feedback_layer(captured):
    layer = _layer()
    v = _violation()
    db = FakeSession(violation=v, layers=[layer])

    pid = _sync(db, v.id, "correct", explanation="Add the mandated disclaimer.",
                final_text="Illustrative returns, not guaranteed.")

    assert len(captured) == 1
    row = captured[0]
    assert row["id"] == pid
    # Mapped onto the ingest schema so retrieval/prompting can't tell the
    # difference between a taught precedent and an ingested one.
    assert row["highlighted_span"] == "Guaranteed 12% returns every year."
    assert row["reviewer_comment"] == "Add the mandated disclaimer."
    assert row["after_text"] == "Illustrative returns, not guaranteed."
    assert row["issue_type"] == "disclosure"
    assert row["severity"] == "high"
    assert row["product_category"] == "ULIP"
    assert row["guideline_ref"] == "IRDAI Adv. Guidelines §5(1)"
    assert row["is_reviewer"] is True
    assert row["source_file"] == rfs.REVIEWER_FEEDBACK_SOURCE_FILE
    assert row["canonical_hash"]  # NOT NULL UNIQUE in precedent_cases

    # ...and stamped into the layer, which is what makes it removable.
    stamps = db.sql_matching("UPDATE precedent_cases SET source_layer_id")
    assert len(stamps) == 1
    assert stamps[0][1]["lid"] == str(layer.id)
    assert stamps[0][1]["ids"] == [pid]


def test_reviewer_comment_falls_back_to_the_finding_when_none_was_typed(captured):
    v = _violation()
    db = FakeSession(violation=v, layers=[_layer()])
    _sync(db, v.id, "correct")
    # An empty reviewer_comment is NOT NULL-illegal, and a thin one is dropped
    # by the retrieval substance filter.
    assert captured[0]["reviewer_comment"] == v.description


def test_the_machine_suggestion_is_the_approved_rewrite_only_when_confirmed(captured):
    v = _violation()
    db = FakeSession(violation=v, layers=[_layer()])
    _sync(db, v.id, "correct")
    assert captured[0]["after_text"] == v.suggested_fix


def test_item_count_is_refreshed_so_the_admin_sees_the_real_size(captured):
    db = FakeSession(violation=_violation(), layers=[_layer()], count=3)
    _sync(db, db.violation.id, "correct")
    assert db.sql_matching("UPDATE corpus_layers SET item_count")


# ===================================================================== polarity ===

def test_not_violation_precedent_teaches_the_opposite_direction(captured):
    v = _violation()
    db = FakeSession(violation=v, layers=[_layer()])

    _sync(db, v.id, "not_violation", reason="valid_regulatory_exception")

    row = captured[0]
    # precedent_cases has no polarity column, so the direction has to be in the
    # text that reaches the prompt AND the embedded signature.
    assert row["issue_type"].startswith("Not a violation")
    assert "NOT a violation" in row["why_rationale"]
    assert "valid_regulatory_exception" in row["why_rationale"]
    assert row["severity"] == "informational"
    # A rejected suggestion is not an approved rewrite.
    assert row["after_text"] is None


def test_not_violation_still_honours_an_explicit_reviewer_rewrite(captured):
    v = _violation()
    db = FakeSession(violation=v, layers=[_layer()])
    _sync(db, v.id, "not_violation", final_text="Reviewer's own wording.")
    assert captured[0]["after_text"] == "Reviewer's own wording."


# ============================================================== one row, always ===

def test_re_actioning_rewrites_the_same_precedent_row(captured):
    v = _violation()
    db = FakeSession(violation=v, layers=[_layer()])

    first = _sync(db, v.id, "correct", explanation="Add the disclaimer.")
    second = _sync(db, v.id, "not_violation", explanation="Actually fine here.")

    assert first == second, "a changed verdict must UPDATE, never duplicate"
    assert len({r["id"] for r in captured}) == 1
    assert len({r["canonical_hash"] for r in captured}) == 1
    # ...and the content genuinely changed.
    assert captured[1]["issue_type"].startswith("Not a violation")


def test_the_precedent_id_is_derived_from_the_violation():
    vid = uuid.uuid4()
    assert rfs._reviewer_precedent_id(vid) == rfs._reviewer_precedent_id(vid)
    assert rfs._reviewer_precedent_id(vid) != rfs._reviewer_precedent_id(uuid.uuid4())
    uuid.UUID(rfs._reviewer_precedent_id(vid))  # a real UUID, it is the PK


# ============================================================== layer lifecycle ===

def test_the_layer_is_created_on_first_use(captured):
    v = _violation()
    db = FakeSession(violation=v, layers=[])

    _sync(db, v.id, "correct")

    assert len(db.layers) == 1
    created = db.layers[0]
    assert created.name == rfs.REVIEWER_FEEDBACK_LAYER_NAME
    assert created.kind == "reviewer_feedback"
    assert created.enabled is True
    # claim=False: an auto-created layer must not adopt unrelated precedents.
    stamps = db.sql_matching("UPDATE precedent_cases SET source_layer_id")
    assert len(stamps) == 1
    assert stamps[0][1]["ids"] == [captured[0]["id"]]


def test_the_layer_is_created_only_once(captured):
    v = _violation()
    db = FakeSession(violation=v, layers=[])

    _sync(db, v.id, "correct")
    _sync(db, _violation(id=v.id).id, "correct")

    assert len(db.layers) == 1


# ===================================================================== removal ===

def test_dismiss_deletes_the_precedent_it_had_taught():
    layer = _layer()
    v = _violation()
    db = FakeSession(violation=v, layers=[layer], rowcount=1)

    assert _sync(db, v.id, "dismiss") is None

    deletes = db.sql_matching("DELETE FROM precedent_cases")
    assert len(deletes) == 1
    sql, params = deletes[0]
    assert "source_layer_id" in sql, "a delete must be scoped to this layer"
    assert params["iid"] == rfs._reviewer_precedent_id(v.id)


def test_dismissing_a_finding_that_taught_nothing_is_a_no_op():
    db = FakeSession(violation=_violation(), layers=[_layer()], rowcount=0)
    assert _sync(db, db.violation.id, "dismiss") is None  # LookupError swallowed


def test_dismiss_never_creates_the_layer():
    db = FakeSession(violation=_violation(), layers=[])
    _sync(db, db.violation.id, "dismiss")
    assert db.layers == []
    assert db.executed == []


# ==================================================================== fail-soft ===

def test_an_embedding_failure_does_not_reach_the_caller(monkeypatch, caplog):
    async def boom(rows):
        raise RuntimeError("embedder unreachable")

    import app.services.rag.indexers.precedent_indexer as indexer
    monkeypatch.setattr(indexer, "upsert_precedents", boom)

    v = _violation()
    db = FakeSession(violation=v, layers=[_layer()])
    with caplog.at_level("ERROR"):
        assert _sync(db, v.id, "correct") is None

    assert db.rollbacks == 1
    assert any("reviewer precedent sync failed" in r.getMessage() for r in caplog.records)


def test_an_unknown_violation_is_a_no_op(captured):
    db = FakeSession(violation=None, layers=[_layer()])
    assert _sync(db, uuid.uuid4(), "dismiss") is None
    assert _sync(db, uuid.uuid4(), "correct") is None
    assert captured == []


def test_the_violation_lookup_is_by_model_not_by_luck():
    # Guards the fake: the service must query the Violation model.
    db = FakeSession(violation=_violation(), layers=[])
    assert db.query(Violation).first() is db.violation
