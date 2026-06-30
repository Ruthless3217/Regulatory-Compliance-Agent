"""Reviewer-voice commentary + novel-finding coverage (2026-05-28 design).

Unit tests for the prompt/schema/corpus-filter changes. No LLM or DB calls —
the golden integration test (test_smart_secure_golden.py) covers end-to-end.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- Schema: PrecedentCitation reviewer voice ---------------------------

def test_precedent_citation_uses_reviewer_comment():
    from app.schemas.compliance_schemas import PrecedentCitation

    c = PrecedentCitation(
        precedent_index=0,
        current_text="comprehensive life coverage up to ₹3 Crore",
        reviewer_comment="Has UW approved the ₹3 Crore SA? Pls share approval on tool.",
        action_type="share-evidence",
        evidence_needed="UW approval",
    )
    assert c.reviewer_comment.startswith("Has UW approved")
    assert c.action_type == "share-evidence"
    assert c.evidence_needed == "UW approval"
    # `description` is replaced by `reviewer_comment` and must no longer exist.
    assert not hasattr(c, "description")


def test_precedent_citation_rejects_bad_action_type():
    from pydantic import ValidationError

    from app.schemas.compliance_schemas import PrecedentCitation

    with pytest.raises(ValidationError):
        PrecedentCitation(
            precedent_index=0,
            current_text="x",
            reviewer_comment="this is a long enough comment to pass min_length",
            action_type="not-a-real-action",
        )


def test_precedent_citation_evidence_needed_optional():
    from app.schemas.compliance_schemas import PrecedentCitation

    c = PrecedentCitation(
        precedent_index=1,
        current_text="switch between different investment funds",
        reviewer_comment="Include clear information: switching between funds is free of the Miscellaneous Charge.",
        action_type="rewrite",
    )
    assert c.evidence_needed is None


# --- Schema: NovelFinding ----------------------------------------------

def test_novel_finding_requires_regulatory_basis():
    from pydantic import ValidationError

    from app.schemas.compliance_schemas import NovelFinding

    with pytest.raises(ValidationError):
        NovelFinding(
            current_text="GST is not applicable on individual life insurance premium",
            reviewer_comment="Tax claim cites a notification — share Tax team approval before publication.",
            action_type="verify-source",
            regulatory_basis="",  # empty — must fail min_length
            confidence=0.85,
        )


def test_novel_finding_valid():
    from app.schemas.compliance_schemas import NovelFinding

    f = NovelFinding(
        current_text="GST is not applicable on individual life insurance premium",
        reviewer_comment=(
            "Tax claim cites Notification 16/2025 — share Tax team approval "
            "substantiating both the notification number and the scope before publication."
        ),
        action_type="verify-source",
        evidence_needed="Tax team approval + scope confirmation",
        regulatory_basis="IRDAI Advertisement Regulations 2021 — tax claim substantiation requirement",
        confidence=0.85,
    )
    assert f.regulatory_basis.startswith("IRDAI")
    assert f.confidence == 0.85


def test_precedent_citations_result_carries_novel_findings():
    from app.schemas.compliance_schemas import PrecedentCitationsResult

    r = PrecedentCitationsResult()
    assert r.citations == []
    assert r.novel_findings == []


# --- Corpus filter: runtime thin-precedent guard ------------------------

def test_response_token_filter():
    from app.services.rag.retrievers.precedent_retriever import _is_thin

    # Pure-response comments (with/without trailing punctuation) → thin.
    assert _is_thin({"comment_text": "done."}) is True
    assert _is_thin({"comment_text": "OK"}) is True
    assert _is_thin({"comment_text": "Added"}) is True
    assert _is_thin({"comment_text": ""}) is True
    assert _is_thin({"comment_text": None}) is True
    assert _is_thin({}) is True

    # Real reviewer flags — short but substantive → kept.
    assert _is_thin({"comment_text": "missing?"}) is False
    assert _is_thin({"comment_text": "incorrect value"}) is False
    assert _is_thin({"comment_text": "Lockin- period Ulip disclaimer missing"}) is False


def test_thin_precedents_filtered_in_retrieval():
    """retrieve_per_chunk drops thin precedents returned by the store."""
    import asyncio

    from app.services.rag.retrievers import precedent_retriever as pr

    real = {"id": "r1", "comment_text": "Lockin disclaimer missing", "document_id": "d1"}
    thin = {"id": "t1", "comment_text": "done.", "document_id": "d1"}

    class _FakeHit:
        def __init__(self, fields):
            self.id = fields["id"]
            self.score = 0.9
            self.fields = fields

    class _FakeStore:
        async def hybrid_search(self, **kwargs):
            return [_FakeHit(real), _FakeHit(thin)]

    class _FakeEmbedder:
        async def embed(self, texts, *args, **kwargs):
            return [[0.0]] * len(texts)

    orig_store = pr.get_vector_store
    orig_embed = pr.get_embedder
    pr.get_vector_store = lambda: _FakeStore()
    pr.get_embedder = lambda: _FakeEmbedder()
    try:
        retriever = pr.PrecedentRetriever()
        out = asyncio.run(
            retriever.retrieve_per_chunk([{"id": "c1", "text": "some draft"}], top_k=5)
        )
    finally:
        pr.get_vector_store = orig_store
        pr.get_embedder = orig_embed

    ids = [p["id"] for p in out["c1"]]
    assert "r1" in ids
    assert "t1" not in ids


# --- Prompt: reviewer-voice structure -----------------------------------

def _svc():
    from app.services.preprocessing_service import ContextEngineeringService

    return ContextEngineeringService(db=None)


def test_reviewer_voice_prompt_structure():
    precedents = [
        {
            "chunk_text": "switch between investment funds based on your goals",
            "anchor_text": "switch between investment funds",
            "comment_text": (
                "Include clear information switching between funds under Investor "
                "Selectable Portfolio Strategy is free of the Miscellaneous Charge."
            ),
            "violation_category": "legal language",
            "severity": "moderate",
        }
    ]
    content = "allows you to switch between different investment funds"
    prompt = _svc().create_precedent_prompts(content, precedents)

    # Negative constraint banning meta-bridge phrasing, with the forbidden phrases.
    assert "similar to a precedent" in prompt
    assert "the precedent flagged" in prompt
    assert "DO NOT" in prompt

    # All five voice examples present.
    for n in range(1, 6):
        assert f"EXAMPLE {n}" in prompt

    # Every action type enumerated.
    for action in ("rewrite", "share-evidence", "add-disclaimer", "verify-source", "remove"):
        assert action in prompt

    # Novel-finding section + the fields it requires.
    assert "novel_findings" in prompt
    assert "regulatory_basis" in prompt
    assert "reviewer_comment" in prompt

    # The new section and the retrieved precedent still appear.
    assert content in prompt
    assert "NEW DOCUMENT SECTION" in prompt
    assert "Investor Selectable Portfolio Strategy" in prompt


def test_empty_precedents_emits_novel_only():
    content = "GST is not applicable on individual life insurance premium"
    prompt = _svc().create_precedent_prompts(content, [])

    # No enumerated precedent block.
    assert "--- PRECEDENT 0" not in prompt
    # Explicit novel-only instruction.
    assert "novel_findings" in prompt
    assert "regulatory_basis" in prompt
    lowered = prompt.lower()
    assert "no precedent" in lowered or "no historical" in lowered
    # Must still tell the model not to emit citations.
    assert "citation" in lowered
    # The section under review is present.
    assert content in prompt


# --- nodes.py: citation + novel-finding → violation mapping -------------

def _result(citations=None, novel_findings=None):
    from app.schemas.compliance_schemas import PrecedentCitationsResult

    return PrecedentCitationsResult(
        citations=citations or [],
        novel_findings=novel_findings or [],
    )


def test_citation_mapping_carries_reviewer_voice_and_metadata():
    from app.schemas.compliance_schemas import PrecedentCitation
    from app.services.agents.graph.nodes import map_findings_to_violations

    precedents = [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "document_id": "ticket-1",
            "source_file": "f.json",
            "anchor_text": "₹3 Crore",
            "comment_text": "Has UW approved this? Pls share approval on tool",
            "final_text_chunk": None,
            "violation_category": "legal language",
            "severity": "critical",
            "score": 0.91,
        }
    ]
    cit = PrecedentCitation(
        precedent_index=0,
        current_text="comprehensive life coverage up to ₹3 Crore",
        reviewer_comment="Has UW approved the ₹3 Crore SA? Pls share approval on tool.",
        action_type="share-evidence",
        evidence_needed="UW approval",
        confidence=0.9,
    )
    vios = map_findings_to_violations(
        _result(citations=[cit]), precedents,
        chunk_id="c1", chunk_index=0, location="chunk:c1",
    )
    assert len(vios) == 1
    v = vios[0]
    # reviewer_comment drives the user-visible description.
    assert v["description"] == "Has UW approved the ₹3 Crore SA? Pls share approval on tool."
    assert v["cited_precedent_id"] == "11111111-1111-1111-1111-111111111111"
    assert v["cited_comment_verbatim"] == "Has UW approved this? Pls share approval on tool"
    assert v["severity"] == "critical"
    md = v["violation_metadata"]
    assert md["grounding"] == "precedent"
    assert md["action_type"] == "share-evidence"
    assert md["evidence_needed"] == "UW approval"


def test_novel_finding_confidence_floor():
    from app.schemas.compliance_schemas import NovelFinding
    from app.services.agents.graph.nodes import map_findings_to_violations

    keep = NovelFinding(
        current_text="GST is not applicable on individual life insurance premium",
        reviewer_comment="Tax claim cites a notification — share Tax team approval before publication.",
        action_type="verify-source",
        evidence_needed="Tax team approval",
        regulatory_basis="IRDAI Advertisement Regulations 2021 — tax claim substantiation",
        confidence=0.80,
    )
    drop = NovelFinding(
        current_text="best plan in the market",
        reviewer_comment="Superlative claim needs substantiation or removal before publication.",
        action_type="remove",
        regulatory_basis="IRDAI Advertisement Regulations 2021 — unsubstantiated superlatives",
        confidence=0.60,  # below the 0.75 floor → dropped
    )
    vios = map_findings_to_violations(
        _result(novel_findings=[keep, drop]), precedents=[],
        chunk_id="c2", chunk_index=1, location="chunk:c2",
    )
    assert len(vios) == 2
    assert vios[0]["suppressed"] is False
    assert vios[1]["suppressed"] is True
    assert "below floor" in vios[1]["suppressed_reason"]
    v = vios[0]
    assert v["current_text"] == "GST is not applicable on individual life insurance premium"
    # Novel findings have NULL citation columns.
    assert v["cited_precedent_id"] is None
    assert v["cited_comment_verbatim"] is None
    md = v["violation_metadata"]
    assert md["grounding"] == "novel"
    assert md["regulatory_basis"].startswith("IRDAI")
    assert md["action_type"] == "verify-source"


# --- Corpus filter: alembic 0007 purge predicate ------------------------

def _load_migration_0007():
    import importlib.util

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "alembic", "versions", "0007_drop_response_precedents.py",
    )
    spec = importlib.util.spec_from_file_location("mig_0007", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_alembic_0007_purge_predicate():
    mig = _load_migration_0007()

    # 4 pure-response comments → purged (mirrors the SQL normalization).
    for thin in ("done.", "OK", "Added", "approved!!"):
        assert mig._is_purged(thin) is True, thin

    # 4 real reviewer flags → preserved.
    for real in ("source?", "pls add source", "rephrase.", "Lockin- period Ulip disclaimer missing"):
        assert mig._is_purged(real) is False, real


def test_alembic_0007_predicate_matches_runtime_guard():
    """The migration's purge set must not be looser than the runtime guard's
    denylist — both should agree on the canonical response tokens."""
    mig = _load_migration_0007()
    from app.services.rag.precedent_filters import RESPONSE_TOKENS

    assert mig.RESPONSE_DENYLIST == RESPONSE_TOKENS


@pytest.mark.skipif(
    os.getenv("RUN_DB_MIGRATION_TESTS") != "1",
    reason="DB-backed dry run; set RUN_DB_MIGRATION_TESTS=1 to enable",
)
def test_alembic_0007_dry_run():
    """End-to-end dry run inside a rolled-back transaction (never mutates the
    real corpus): seed 4 pure-response + 4 real rows, run the migration's
    snapshot+delete SQL, assert only the response rows are removed, then
    restore from the audit table and assert they come back."""
    import os as _os

    from sqlalchemy import text

    from app.database import engine

    mig = _load_migration_0007()
    arr = mig._denylist_array_sql()
    norm = mig._NORM
    dim = int(_os.getenv("RAG_EMBEDDING_DIM", "1536"))
    zero_vec = "[" + ",".join("0" for _ in range(dim)) + "]"
    audit = "_purged_dry_run"  # distinct from the real migration's audit table
    doc = "dryrun-0007-sentinel"

    response_comments = ["done.", "OK", "Added", "approved!!"]
    real_comments = ["source?", "pls add source", "rephrase.", "Lockin disclaimer missing"]

    conn = engine.connect()
    trans = conn.begin()
    try:
        for i, cmt in enumerate(response_comments + real_comments):
            conn.execute(
                text(
                    "INSERT INTO rag_compliance_examples "
                    "(document_id, chunk_text, comment_text, source_file, embed_text, embedding) "
                    "VALUES (:doc, :chunk, :cmt, :sf, :et, CAST(:emb AS vector))"
                ),
                {"doc": doc, "chunk": f"chunk {i}", "cmt": cmt,
                 "sf": "dryrun.json", "et": cmt, "emb": zero_vec},
            )

        def _count(where_extra: str = "") -> int:
            return conn.execute(
                text(f"SELECT count(*) FROM rag_compliance_examples WHERE document_id = :doc {where_extra}"),
                {"doc": doc},
            ).scalar()

        assert _count() == 8

        # Snapshot + delete (migration upgrade body, scoped audit table name).
        conn.execute(text(
            f"CREATE TABLE {audit} AS SELECT * FROM rag_compliance_examples WHERE {norm} = ANY({arr})"
        ))
        conn.execute(text(f"DELETE FROM rag_compliance_examples WHERE {norm} = ANY({arr})"))

        # Only the 4 real rows remain for our sentinel doc.
        assert _count() == 4
        remaining = {
            r[0] for r in conn.execute(
                text("SELECT comment_text FROM rag_compliance_examples WHERE document_id = :doc"),
                {"doc": doc},
            )
        }
        assert remaining == set(real_comments)

        # Restore from audit (migration downgrade body).
        conn.execute(text(
            f"INSERT INTO rag_compliance_examples SELECT * FROM {audit} ON CONFLICT (id) DO NOTHING"
        ))
        assert _count() == 8
    finally:
        trans.rollback()  # undoes seeds, deletes, and the audit table
        conn.close()
