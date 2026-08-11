"""Chunk-level analysis reuse: an untouched chunk keeps last run's verdicts.

Re-analysing a document used to re-grade every chunk at ~3 LLM calls each, even
the paragraphs nobody touched — and, thanks to the early-return in
preprocess_submission, it graded the text as UPLOADED rather than as edited.
These pin the two halves of the fix:

  * preprocessing reconciles chunk rows against the current content, so an
    unchanged chunk KEEPS ITS ID (violations.chunk_id and the cache both hang
    off that identity) while an edited one is replaced;
  * a chunk is reused only when its text and its entire grading context are
    identical to the last PERSISTED run — text, prompt version, models, flags,
    retrieval config, rule/precedent corpus, and the cross-chunk document
    context, which embeds other chunks' text into this chunk's prompt.

The dangerous failure is not a missed cache hit (that costs money); it is a
stale hit, which reports a verdict about text the document no longer contains.
Every test below is a way that must not happen.
"""
import asyncio
import uuid

import pytest

from app.services.agents.compliance import analysis_cache as cache
from app.services.agents.graph import nodes


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Settings:
    """The settings fields the fingerprint reads, at their production defaults."""
    llm_provider = "azure"
    llm_model = "gpt-5.4"
    llm_reasoning_effort = "medium"
    critic_llm_provider = ""
    critic_llm_model = "gpt-5.4-nano"
    critic_llm_reasoning_effort = ""
    critic_enabled = True
    llm_critic_enabled = True
    completeness_sweep_enabled = True
    product_grounding_enabled = True
    cross_chunk_context_enabled = True
    cross_chunk_context_token_budget = 8000
    pgvector_top_k = 15
    rag_top_k_analysis = 8
    rag_min_cosine = 0.25
    rag_min_ts_rank = 0.02
    rag_rrf_k = 60
    product_docs_top_k = 3
    grade_concurrency = 6
    analysis_reuse_enabled = True


RULES = {"irdai": [{"id": "r1", "rule_text": "No guaranteed returns.",
                    "severity": "high", "category": "irdai", "product_line": None}]}


def _chunk(idx, text, cid=None):
    return {"id": cid or str(uuid.uuid4()), "chunk_index": idx, "text": text, "metadata": {}}


def _state_of(chunks):
    return {
        "submission_id": str(uuid.uuid4()),
        "user_id": None,
        "chunks": chunks,
        "active_rules": RULES,
        "chunk_rules": {},
        "retrieved_examples": {},
        "product_facts": [],
        "product_passages": {},
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def test_identical_chunk_and_context_produce_the_same_key():
    fp = cache.run_context_fingerprint(_Settings(), active_rules=RULES)
    a = cache.chunk_context_key(fp, "Guaranteed 12% returns.", "[chunk 1] footer")
    b = cache.chunk_context_key(fp, "Guaranteed 12% returns.", "[chunk 1] footer")
    assert a == b


def test_edited_chunk_text_changes_the_key():
    fp = cache.run_context_fingerprint(_Settings(), active_rules=RULES)
    before = cache.chunk_context_key(fp, "Guaranteed 12% returns.", "ctx")
    after = cache.chunk_context_key(fp, "Indicative 12% returns.", "ctx")
    assert before != after


def test_whitespace_only_edit_still_changes_the_key():
    """The hash is NOT normalised on purpose: a carried-forward finding quotes
    `current_text` verbatim, and the grounding guard drops a quote that is not
    literally present in the chunk."""
    fp = cache.run_context_fingerprint(_Settings(), active_rules=RULES)
    assert cache.chunk_context_key(fp, "a  b", "ctx") != cache.chunk_context_key(fp, "a b", "ctx")


def test_document_context_change_invalidates_an_untouched_chunk():
    """Cross-chunk context means editing chunk 7 can change how chunk 2 grades."""
    fp = cache.run_context_fingerprint(_Settings(), active_rules=RULES)
    same_text = "Invest with confidence."
    before = cache.chunk_context_key(fp, same_text, "[chunk 7] old footer")
    after = cache.chunk_context_key(fp, same_text, "[chunk 7] new footer")
    assert before != after


def test_rule_edit_changes_the_run_fingerprint():
    base = cache.run_context_fingerprint(_Settings(), active_rules=RULES)
    bumped = cache.run_context_fingerprint(
        _Settings(),
        active_rules={"irdai": [{**RULES["irdai"][0], "rule_text": "No guaranteed returns whatsoever."}]},
    )
    assert base != bumped


def test_new_precedent_or_model_change_changes_the_run_fingerprint():
    base = cache.run_context_fingerprint(_Settings(), active_rules=RULES)

    with_precedent = cache.run_context_fingerprint(
        _Settings(), active_rules=RULES, retrieved_examples={"c1": [{"id": "p1"}]}
    )
    assert base != with_precedent

    class _Upgraded(_Settings):
        llm_model = "gpt-6"

    assert base != cache.run_context_fingerprint(_Upgraded(), active_rules=RULES)


def test_prompt_version_bump_invalidates_everything():
    fp_old = cache.run_context_fingerprint(_Settings(), active_rules=RULES)
    original = cache.PROMPT_VERSION
    try:
        cache.PROMPT_VERSION = original + "-next"
        assert cache.run_context_fingerprint(_Settings(), active_rules=RULES) != fp_old
    finally:
        cache.PROMPT_VERSION = original


# ---------------------------------------------------------------------------
# plan_chunk_reuse — the whole decision, as a pure function
# ---------------------------------------------------------------------------

def test_unchanged_chunk_is_reused_with_its_findings():
    hits = cache.plan_chunk_reuse(
        current_keys={"c1": "k1"},
        stored_keys={"c1": "k1"},
        prior_violations={"c1": [{"description": "guarantee"}]},
    )
    assert list(hits) == ["c1"]
    assert hits["c1"] == [{"description": "guarantee"}]


def test_chunk_graded_clean_last_time_is_still_a_hit():
    """'No findings' is a verdict too — an empty list must not read as a miss,
    or every clean chunk would be re-graded forever."""
    hits = cache.plan_chunk_reuse({"c1": "k1"}, {"c1": "k1"}, {})
    assert hits == {"c1": []}


def test_changed_chunk_is_not_reused():
    hits = cache.plan_chunk_reuse({"c1": "k2"}, {"c1": "k1"}, {"c1": [{"d": 1}]})
    assert hits == {}


def test_new_chunk_is_not_reused():
    hits = cache.plan_chunk_reuse({"c1": "k1", "c2": "k9"}, {"c1": "k1"}, {})
    assert list(hits) == ["c1"]


def test_chunk_never_analysed_is_not_reused():
    """A NULL stored key (pre-0035 row, or a run that never persisted) must miss:
    carrying nothing forward would silently certify the chunk as clean."""
    assert cache.plan_chunk_reuse({"c1": "k1"}, {"c1": None}, {"c1": []}) == {}


def test_deleted_chunk_drops_its_verdicts():
    """The chunk is gone from the document, so it is absent from current_keys —
    its findings are simply never carried onto the new check."""
    hits = cache.plan_chunk_reuse(
        current_keys={"c1": "k1"},
        stored_keys={"c1": "k1", "gone": "kg"},
        prior_violations={"c1": [], "gone": [{"description": "was here"}]},
    )
    assert "gone" not in hits
    assert not any(v for vs in hits.values() for v in vs)


def test_context_key_change_invalidates_every_chunk():
    """A rule edit / model swap moves the run fingerprint, so no chunk hits."""
    old_fp = cache.run_context_fingerprint(_Settings(), active_rules=RULES)
    new_fp = cache.run_context_fingerprint(
        _Settings(),
        active_rules={"irdai": [{**RULES["irdai"][0], "rule_text": "edited"}]},
    )
    chunks = {"c1": "one", "c2": "two"}
    stored = {cid: cache.chunk_context_key(old_fp, t, "ctx") for cid, t in chunks.items()}
    current = {cid: cache.chunk_context_key(new_fp, t, "ctx") for cid, t in chunks.items()}
    assert cache.plan_chunk_reuse(current, stored, {}) == {}


# ---------------------------------------------------------------------------
# Chunk reconciliation (the prerequisite: re-chunk on content change)
# ---------------------------------------------------------------------------

class _FakeDB:
    def __init__(self):
        self.added, self.deleted = [], []

    def add(self, obj):
        self.added.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)


def _row(idx, text):
    from app.models.content_chunk import ContentChunk

    return ContentChunk(
        id=uuid.uuid4(), chunk_index=idx, text=text,
        content_hash=cache.chunk_content_hash(text), chunk_metadata={},
    )


def _sync(existing, texts):
    from app.services.preprocessing_service import ContextEngineeringService

    db = _FakeDB()
    svc = ContextEngineeringService(db)
    chunks = [{"text": t, "token_count": len(t), "metadata": {}} for t in texts]
    hashes = [cache.chunk_content_hash(t) for t in texts]
    count = svc._sync_chunks(uuid.uuid4(), existing, chunks, hashes)
    return db, count


def test_untouched_chunks_keep_their_row_id_when_a_sibling_is_edited():
    rows = [_row(0, "intro"), _row(1, "old body"), _row(2, "footer")]
    kept_ids = {rows[0].id, rows[2].id}

    db, count = _sync(rows, ["intro", "new body", "footer"])

    assert count == 3
    assert rows[1] in db.deleted and len(db.deleted) == 1
    # The two survivors are re-added (unchanged ids), the edited one is new.
    assert kept_ids <= {getattr(o, "id", None) for o in db.added}
    fresh = [o for o in db.added if o.id not in kept_ids]
    assert len(fresh) == 1 and fresh[0].text == "new body"
    assert fresh[0].content_hash == cache.chunk_content_hash("new body")


def test_removed_section_deletes_its_row():
    rows = [_row(0, "intro"), _row(1, "body"), _row(2, "footer")]
    db, count = _sync(rows, ["intro", "footer"])
    assert count == 2
    assert [r.text for r in db.deleted] == ["body"]
    assert rows[2].chunk_index == 1  # footer moved up, same row


def test_moved_chunk_keeps_its_id_but_is_reindexed():
    rows = [_row(0, "a"), _row(1, "b")]
    db, _ = _sync(rows, ["b", "a"])
    assert db.deleted == []
    assert (rows[1].chunk_index, rows[0].chunk_index) == (0, 1)


def test_legacy_rows_without_a_hash_are_replaced():
    """Pre-0035 rows the migration could not reach: nothing to match on, so they
    are rebuilt rather than trusted."""
    legacy = _row(0, "intro")
    legacy.content_hash = None
    db, count = _sync([legacy], ["intro"])
    assert count == 1 and db.deleted == [legacy]


# ---------------------------------------------------------------------------
# analysis_node — a fully reused document costs zero LLM calls
# ---------------------------------------------------------------------------

def test_reused_chunks_skip_the_llm_and_carry_their_findings(monkeypatch):
    from app.config import settings

    chunks = [_chunk(0, "Guaranteed 12% returns."), _chunk(1, "Terms and conditions apply.")]
    state = _state_of(chunks)

    # The keys the node will compute, mirrored through the public helpers.
    from app.services.preprocessing_service import build_document_context

    fp = cache.run_context_fingerprint(
        settings, active_rules=RULES, retrieved_examples={}, product_facts=[]
    )
    stored = {
        c["id"]: cache.chunk_context_key(
            fp,
            c["text"],
            build_document_context(chunks, c["chunk_index"], settings.cross_chunk_context_token_budget)
            if settings.cross_chunk_context_enabled else None,
        )
        for c in chunks
    }
    prior = {chunks[0]["id"]: [{"description": "guarantee", "severity": "critical",
                                "confidence": 0.9, "reused_violation_id": "v1",
                                "violation_metadata": {"grounding": "rule"}}]}

    monkeypatch.setattr(nodes, "_load_reuse_inputs", lambda *a, **k: (stored, prior))

    def _boom(*a, **k):
        raise AssertionError("a reused chunk must not open a DB session or call the LLM")

    monkeypatch.setattr("app.database.SessionLocal", _boom)
    monkeypatch.setattr(
        "app.services.llm_service.llm_service.generate_structured_response", _boom
    )

    result = asyncio.run(nodes.analysis_node(state))

    md = result["metadata"]
    assert (md["chunks_total"], md["chunks_reused"], md["chunks_analyzed"]) == (2, 2, 0)
    assert md["llm_calls_saved"] == 2 * cache.llm_calls_per_chunk(settings)
    assert md["analysis_failed_chunks"] == 0
    assert md["analysis_context_key"] == fp
    assert md["chunk_keys"] == stored
    assert [v["description"] for v in result["violations"]] == ["guarantee"]
    assert result["violations"][0]["reused_violation_id"] == "v1"


def test_edited_chunk_is_re_analysed_while_its_neighbour_is_reused(monkeypatch):
    """Only the chunk whose key moved reaches the grading path.

    That path is stubbed out at its first step (SessionLocal), so the edited
    chunk lands in `analysis_failed_chunks` — which is precisely the proof that
    it went to the grader instead of being served from cache, while its
    neighbour came back for free.
    """
    from app.config import settings
    from app.services.preprocessing_service import build_document_context

    chunks = [_chunk(0, "Guaranteed 12% returns."), _chunk(1, "Terms and conditions apply.")]
    fp = cache.run_context_fingerprint(
        settings, active_rules=RULES, retrieved_examples={}, product_facts=[]
    )
    stored = {
        chunks[1]["id"]: cache.chunk_context_key(
            fp,
            chunks[1]["text"],
            build_document_context(chunks, 1, settings.cross_chunk_context_token_budget)
            if settings.cross_chunk_context_enabled else None,
        ),
        chunks[0]["id"]: "stale-key-from-before-the-edit",
    }
    monkeypatch.setattr(nodes, "_load_reuse_inputs", lambda *a, **k: (stored, {}))

    def _no_db():
        raise RuntimeError("grading path reached (no DB in this test)")

    monkeypatch.setattr("app.database.SessionLocal", _no_db)

    result = asyncio.run(nodes.analysis_node(_state_of(chunks)))

    md = result["metadata"]
    assert (md["chunks_reused"], md["chunks_analyzed"]) == (1, 1)
    assert md["analysis_failed_chunks"] == 1
    assert md["llm_calls_saved"] == cache.llm_calls_per_chunk(settings)


def test_reuse_can_be_switched_off_in_one_place(monkeypatch):
    from app.config import settings

    chunks = [_chunk(0, "Guaranteed 12% returns.")]
    monkeypatch.setattr(settings, "analysis_reuse_enabled", False)
    monkeypatch.setattr(
        nodes, "_load_reuse_inputs",
        lambda *a, **k: pytest.fail("the cache must not even be consulted"),
    )
    monkeypatch.setattr("app.database.SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("no DB")))

    md = asyncio.run(nodes.analysis_node(_state_of(chunks)))["metadata"]
    assert (md["chunks_reused"], md["chunks_analyzed"]) == (0, 1)
    # Keys are still computed and stamped, so flipping the flag back on works
    # immediately instead of needing a throwaway run to prime the cache.
    assert md["chunk_keys"] and md["analysis_context_key"]


def test_unreadable_cache_fails_open(monkeypatch):
    """A cache lookup that explodes costs LLM calls, never correctness."""
    def _explode(*a, **k):
        raise RuntimeError("database is down")

    monkeypatch.setattr(nodes, "_load_reuse_inputs", _explode)
    monkeypatch.setattr("app.database.SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("no DB")))

    md = asyncio.run(nodes.analysis_node(_state_of([_chunk(0, "text")])))["metadata"]
    assert (md["chunks_reused"], md["chunks_analyzed"]) == (0, 1)


# ---------------------------------------------------------------------------
# Re-parenting: a reused finding keeps its id, and its reviewer's verdict
# ---------------------------------------------------------------------------

class _RowQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _ReparentDB:
    def __init__(self, row):
        self._row = row
        self.added = []

    def query(self, _model):
        return _RowQuery([self._row] if self._row is not None else [])

    def add(self, obj):
        self.added.append(obj)


def test_reused_finding_is_reparented_not_copied():
    from app.models.violation import Violation
    from app.services.agents.compliance.engine import ComplianceEngine

    old_check, new_check, run = uuid.uuid4(), uuid.uuid4(), str(uuid.uuid4())
    row = Violation(
        id=uuid.uuid4(), compliance_check_id=old_check, category="irdai",
        severity="critical", description="guarantee", review_status="accepted",
        chunk_index=3,
    )
    db = _ReparentDB(row)

    handled = ComplianceEngine._reparent_reused(
        {"reused_violation_id": str(row.id), "chunk_index": 1, "location": "chunk:x"},
        new_check, run, db,
    )

    assert handled is True
    assert row.compliance_check_id == new_check
    assert row.analysis_run_id == run
    assert row.chunk_index == 1 and row.location == "chunk:x"
    # The reviewer's verdict rides along on the same row id.
    assert row.review_status == "accepted"
    assert db.added == [row]


def test_missing_reused_row_falls_back_to_a_fresh_insert():
    """Fail open: a vanished row costs a reviewer verdict, never the finding."""
    from app.services.agents.compliance.engine import ComplianceEngine

    assert ComplianceEngine._reparent_reused(
        {"reused_violation_id": str(uuid.uuid4())}, uuid.uuid4(), None, _ReparentDB(None)
    ) is False


def test_fresh_finding_is_not_treated_as_reused():
    from app.services.agents.compliance.engine import ComplianceEngine

    assert ComplianceEngine._reparent_reused({"description": "new"}, uuid.uuid4(), None, _ReparentDB(None)) is False
