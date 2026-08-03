"""Generated rules may publish only their exact supporting source quote."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.rag.errors import RAGIndexingFailed
from app.services.rag.indexers import source_docs_indexer
from app.services.rule_generator_service import (
    ExtractedRule,
    RuleExtractionResult,
    RuleGeneratorService,
)


def _document(quote: str) -> str:
    return (
        "Regulatory circular for insurance advertisements. "
        f"{quote} "
        "This circular applies to all marketing communications and intermediaries."
    )


def _generated_rule(source_quote: str) -> ExtractedRule:
    return ExtractedRule(
        rule_text="Disclose all applicable charges.",
        category="regulatory",
        severity="high",
        keywords=["charges", "disclose"],
        source_quote=source_quote,
    )


def test_generation_stages_exact_quote_and_persists_per_rule_mapping():
    quote = "Every advertisement must disclose all applicable charges."
    document = _document(quote)
    passage_id = uuid.uuid4()
    fake_rule = SimpleNamespace(
        id=uuid.uuid4(),
        category="regulatory",
        rule_text="Disclose all applicable charges.",
        severity="high",
        keywords=["charges", "disclose"],
        points_deduction=-5.0,
        product_line="global",
    )
    service = RuleGeneratorService()
    service.llm = SimpleNamespace(
        generate_structured_response=AsyncMock(
            return_value=RuleExtractionResult(rules=[_generated_rule(quote)])
        )
    )

    with patch(
        "app.services.rag.indexers.source_docs_indexer.index_source_document",
        new=AsyncMock(return_value=[]),
    ), patch(
        "app.services.rag.indexers.source_docs_indexer.index_source_evidence_quote",
        new=AsyncMock(return_value=passage_id),
    ) as index_evidence, patch.object(
        service, "create_rule", return_value=fake_rule
    ) as create_rule, patch(
        "app.services.rag.indexers.rules_indexer.upsert_rule",
        new=AsyncMock(),
    ):
        result = asyncio.run(service.generate_rules_from_text(
            document_content=document,
            document_title="Circular 1",
            created_by_user_id=None,
            db=MagicMock(),
            product_line="global",
        ))

    assert result["success"] is True
    assert result["source_evidence_indexed"] == 1
    assert result["rules"][0]["source_quote"] == quote
    index_evidence.assert_awaited_once()
    assert index_evidence.await_args.kwargs["source_quote"] == quote
    metadata = create_rule.call_args.kwargs["metadata"]
    assert metadata["source_quote"] == quote
    assert metadata["source_evidence_passage_id"] == str(passage_id)


def test_generation_rejects_paraphrased_quote_before_creating_draft():
    exact_quote = "Every advertisement must disclose all applicable charges."
    service = RuleGeneratorService()
    service.llm = SimpleNamespace(
        generate_structured_response=AsyncMock(
            return_value=RuleExtractionResult(
                rules=[_generated_rule("Advertisements should mention fees.")]
            )
        )
    )

    with patch(
        "app.services.rag.indexers.source_docs_indexer.index_source_document",
        new=AsyncMock(return_value=[]),
    ), patch(
        "app.services.rag.indexers.source_docs_indexer.index_source_evidence_quote",
        new=AsyncMock(),
    ) as index_evidence, patch.object(
        service, "create_rule"
    ) as create_rule:
        result = asyncio.run(service.generate_rules_from_text(
            document_content=_document(exact_quote),
            document_title="Circular 1",
            created_by_user_id=None,
            db=MagicMock(),
            product_line="global",
        ))

    assert result["success"] is False
    assert result["rules_created"] == 0
    assert result["rules_failed"] == 1
    assert "exact verbatim source_quote" in result["errors"][0]
    index_evidence.assert_not_awaited()
    create_rule.assert_not_called()


def test_quote_indexer_stores_only_exact_quote_as_unpublished_evidence():
    quote = "Every advertisement must disclose all applicable charges."
    embedder = SimpleNamespace(embed=AsyncMock(return_value=[[0.1, 0.2]]))
    store = SimpleNamespace(upsert=AsyncMock())

    with patch.object(source_docs_indexer, "get_embedder", return_value=embedder), patch.object(
        source_docs_indexer, "get_vector_store", return_value=store
    ):
        passage_id = asyncio.run(source_docs_indexer.index_source_evidence_quote(
            document_id=uuid.uuid4(),
            document_title="Circular 1",
            regulator="irdai",
            full_text=_document(quote),
            source_quote=quote,
            evidence_index=3,
        ))

    uuid.UUID(passage_id)
    _, docs = store.upsert.await_args.args
    assert len(docs) == 1
    assert docs[0].fields["text"] == quote
    assert docs[0].fields["derived_rule_ids"] == []


def test_quote_indexer_rejects_text_not_present_in_source():
    with pytest.raises(RAGIndexingFailed):
        asyncio.run(source_docs_indexer.index_source_evidence_quote(
            document_id=uuid.uuid4(),
            document_title="Circular 1",
            regulator="irdai",
            full_text=_document("Disclose charges."),
            source_quote="A paraphrase absent from the circular.",
            evidence_index=0,
        ))


def test_approval_link_matches_passage_document_and_exact_quote():
    db = MagicMock()
    db.execute.return_value.rowcount = 1
    quote = "Every advertisement must disclose all applicable charges."

    with patch.object(source_docs_indexer, "SessionLocal", return_value=db):
        asyncio.run(source_docs_indexer.link_rule_to_source_quote(
            passage_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            source_quote=quote,
            rule_id=uuid.uuid4(),
        ))

    statement, params = db.execute.call_args.args
    sql = str(statement)
    assert "WHERE id = CAST(:passage_id AS UUID)" in sql
    assert "AND document_id = CAST(:document_id AS UUID)" in sql
    assert "AND text = :source_quote" in sql
    assert params["source_quote"] == quote
    db.commit.assert_called_once()


def test_approval_link_refuses_stale_or_missing_evidence_row():
    db = MagicMock()
    db.execute.return_value.rowcount = 0

    with patch.object(source_docs_indexer, "SessionLocal", return_value=db), pytest.raises(
        RAGIndexingFailed, match="mapping was missing"
    ):
        asyncio.run(source_docs_indexer.link_rule_to_source_quote(
            passage_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            source_quote="Exact quote",
            rule_id=uuid.uuid4(),
        ))

    db.rollback.assert_called_once()
    db.commit.assert_not_called()
