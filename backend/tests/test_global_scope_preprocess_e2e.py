"""The whole refusal chain, driven through the real preprocess node.

SYNTHETIC REPRODUCTION of the 2026-08-30 deployed failure. The unit tests in
test_global_scope_not_a_conflict.py pin the scope decision; this one runs the
node that composes it — resolver -> unresolved signals -> scope signals ->
`metadata["degraded"]` -> `evaluate_persistability` — because the production bug
lived in that composition, not in any single function.

The database, the chunker and the RAG indexer are stubbed. The fact-card
corpus, the resolver, the scope builder and the persistability gate are real.
"""
import asyncio
import uuid
from pathlib import Path

import pytest

from app.services.agents.compliance.engine import ComplianceEngine
from app.services.agents.graph import nodes as graph_nodes
from app.services.agents.graph.context import GraphContext
from app.services.fact_card_service import FactCardService

CARDS_DIR = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"

MULTI_PRODUCT_DOC = """Bajaj Life product range — comparison sheet.

Bajaj Life Invest Protect Goal III (UIN: 116L205V01) is a unit linked plan.
Bajaj Life Smart Secure ROP (UIN: 116L215V01) offers return of premium.
Bajaj Life ACE (UIN: 116N186V04) is a participating savings plan.

Guaranteed returns of 12% every year. India's No.1 insurer.
"""


class _Chunk:
    def __init__(self, text, index):
        self.id = uuid.uuid4()
        self.text = text
        self.chunk_index = index
        self.chunk_metadata = {}


class _ChunkQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_):
        return self

    def order_by(self, *_):
        return self

    def all(self):
        return self._rows


class _Db:
    def __init__(self, rows):
        self._rows = rows

    def query(self, _target):
        return _ChunkQuery(self._rows)


@pytest.fixture
def graph_env(monkeypatch):
    """Real product grounding, stubbed persistence."""
    from app.config import settings
    from app.services import fact_card_service as fcs
    from app.services import preprocessing_service
    from app.services.rag.indexers import chunks_indexer

    monkeypatch.setattr(settings, "product_grounding_enabled", True)
    monkeypatch.setattr(fcs, "get_fact_card_service", lambda: FactCardService(CARDS_DIR))

    async def _no_index(**_kwargs):
        return 0

    monkeypatch.setattr(chunks_indexer, "upsert_chunks_for_submission", _no_index)

    class _Chunker:
        def __init__(self, _db):
            pass

        async def preprocess_submission(self, _submission_id):
            return 1

    monkeypatch.setattr(preprocessing_service, "ContextEngineeringService", _Chunker)

    def _run(text, declared_product_line):
        db = _Db([_Chunk(text, 0)])
        token = GraphContext.set_db_session(db)
        try:
            return asyncio.run(
                graph_nodes.preprocess_node({
                    "submission_id": str(uuid.uuid4()),
                    "metadata": {"declared_product_line": declared_product_line},
                })
            )
        finally:
            GraphContext.reset(token)

    return _run


def _final_state(preprocess_out, violations=()):
    """The graph state the engine sees after the analysis nodes have run."""
    return {
        "chunks": preprocess_out["chunks"],
        "violations": list(violations),
        "scores": {"overall": 58},
        "status": "completed",
        "metadata": preprocess_out["metadata"],
    }


def test_global_multi_product_submission_reaches_a_grade(graph_env):
    """The production condition, end to end: it must now be persistable."""
    out = graph_env(MULTI_PRODUCT_DOC, "global")
    md = out["metadata"]

    assert [m["uin"] for m in md["product_match"]] == [
        "116L205V01", "116L215V01", "116N186V04",
    ]
    assert "degraded" not in md
    assert "product_unresolved" not in md
    assert ComplianceEngine.evaluate_persistability(
        _final_state(out, [{"description": "Guaranteed returns claim"}])
    ) == (True, None)


def test_the_narrowing_is_recorded_on_the_run_for_audit(graph_env):
    """Removing the refusal must not remove the fact that it was a global file."""
    out = graph_env(MULTI_PRODUCT_DOC, "global")

    narrowed = out["metadata"]["scope_narrowed_from_global"]
    assert narrowed["declared"] == "global"
    assert narrowed["uins"] == ["116L205V01", "116L215V01", "116N186V04"]
    assert narrowed["categories"] == ["par", "savings_endowment", "ulip"]

    audit = ComplianceEngine.run_metadata_from_state(_final_state(out))
    assert audit["scope_narrowed_from_global"] == narrowed
    assert audit["declared_product_line"] == "global"


def test_a_generic_document_filed_as_global_is_unaffected(graph_env):
    # Deliberately free of any product name: the fuzzy matcher resolves on the
    # shared "Bajaj Life ..." prefix alone, which is its own (pre-existing)
    # sensitivity and not what this test is about.
    out = graph_env("Insurance is the subject matter of solicitation. T&C apply.", "global")

    assert out["metadata"]["product_match"] == []
    assert "degraded" not in out["metadata"]
    assert "scope_narrowed_from_global" not in out["metadata"]


def test_unknown_uin_under_global_grades_with_a_named_warning(graph_env):
    """An unknown UIN beside three resolved products is an EVIDENCE gap, not an
    unprovable scope — the envelope is proven by the products that did resolve
    (and by the global filing). It used to refuse, which discarded a complete
    analysis of the three grounded products over the fourth.

    The fail-closed guarantee it was reaching for is still enforced, twice
    over: the unknown UIN is never grounded against another product's card, and
    the run cannot be certified — see
    tests/test_partial_analysis_unresolved_products.py.
    """
    out = graph_env(MULTI_PRODUCT_DOC + "\nMystery Plan (UIN: 116N999V01).\n", "global")
    md = out["metadata"]

    assert "degraded" not in md
    assert md["product_unresolved"]["unknown_uins"] == ["116N999V01"]
    warning = next(w for w in md["analysis_warnings"] if w["code"] == "unknown_uins")
    assert warning["detail"]["uins"] == ["116N999V01"]
    assert ComplianceEngine.evaluate_persistability(_final_state(out)) == (True, None)


def test_wrong_declared_family_still_refuses_to_grade(graph_env):
    """Fail-closed guarantee: a term filing is not graded against ULIP rules."""
    out = graph_env(MULTI_PRODUCT_DOC, "term")
    md = out["metadata"]

    assert md["degraded"] == "product_unresolved"
    assert "conflicts with detected scope" in md["product_unresolved"]["submission_scope"][0]
    assert ComplianceEngine.evaluate_persistability(_final_state(out)) == (
        False, "product_unresolved",
    )


def test_a_correct_family_declaration_still_grades(graph_env):
    out = graph_env(MULTI_PRODUCT_DOC, "ulip")

    assert "degraded" not in out["metadata"]
    assert ComplianceEngine.evaluate_persistability(_final_state(out)) == (True, None)


# --------------------------------------------------------------------------
# The audit record must survive all the way onto the AnalysisRun row, not just
# into graph state — the graded run is the one whose provenance matters.
# --------------------------------------------------------------------------


def test_scope_narrowing_is_written_onto_the_analysis_run_row(graph_env, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from app.models.analysis_run import AnalysisRun
    from app.services import run_tracker

    out = graph_env(MULTI_PRODUCT_DOC, "global")
    can_persist, _ = ComplianceEngine.evaluate_persistability(_final_state(out))
    assert can_persist, "the graded path is the one this record has to survive"

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = MagicMock(
        prompt=1, completion=1, cost=0.0
    )
    monkeypatch.setattr(run_tracker.audit, "record", AsyncMock())

    run = AnalysisRun(
        submission_id=uuid.uuid4(), run_number=1, status="running",
        started_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )
    asyncio.run(run_tracker.close_run(db, run, {
        "status": "completed",
        "check_id": str(uuid.uuid4()),
        # Exactly what engine.analyze_submission hands close_run on success.
        "run_metadata": ComplianceEngine.run_metadata_from_state(_final_state(out)),
    }))

    assert run.status == "completed"
    assert run.run_metadata["scope_narrowed_from_global"] == {
        "declared": "global",
        "uins": ["116L205V01", "116L215V01", "116N186V04"],
        "categories": ["par", "savings_endowment", "ulip"],
    }
    assert db.commit.called
