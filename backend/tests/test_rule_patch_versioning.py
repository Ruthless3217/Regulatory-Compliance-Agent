"""PATCH /rules/{id} content-change branch.

An edit to severity/rule_text creates a NEW rule version row and retires the
old one. Two things must happen alongside that today they didn't:

1. Reliability (Beta-Binomial `reliability_alpha`/`beta`) must carry forward
   onto the new row — an edit isn't a fresh rule, so it shouldn't reset
   everything reviewers already taught it back to "no feedback history".
2. The OLD row's RAG index entry must be cleaned up once it's deactivated,
   exactly like DELETE /rules/{id} already does — otherwise a superseded rule
   version stays live in retrieval forever.
"""
import asyncio
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes import rules as rules_routes
from app.models.rule import Rule
from app.services.rule_generator_service import RuleGeneratorService


def _old_rule(**overrides) -> Rule:
    rule = Rule(
        id=uuid.uuid4(),
        category="regulatory",
        rule_text="old text",
        severity="medium",
        keywords=["a"],
        is_active=True,
        product_line="global",
        version=1,
        reliability_alpha=Decimal("7.00"),
        reliability_beta=Decimal("2.00"),
    )
    for k, v in overrides.items():
        setattr(rule, k, v)
    return rule


def _db_returning(rule: Rule) -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = rule
    return db


def test_content_change_copies_reliability_forward():
    old = _old_rule()
    db = _db_returning(old)

    with patch.object(rules_routes, "_safe_rag_upsert", new=AsyncMock()), \
         patch.object(rules_routes, "_safe_rag_delete", new=AsyncMock()):
        asyncio.run(rules_routes.update_rule(
            rule_id=str(old.id), rule_text="new text", user={}, db=db,
        ))

    new_rule = db.add.call_args[0][0]
    assert new_rule is not old
    assert new_rule.reliability_alpha == old.reliability_alpha
    assert new_rule.reliability_beta == old.reliability_beta


def test_content_change_deletes_old_rows_stale_rag_entry():
    old = _old_rule()
    db = _db_returning(old)

    with patch.object(rules_routes, "_safe_rag_upsert", new=AsyncMock()) as upsert, \
         patch.object(rules_routes, "_safe_rag_delete", new=AsyncMock()) as delete:
        asyncio.run(rules_routes.update_rule(
            rule_id=str(old.id), severity="high", user={}, db=db,
        ))

    # Old (now-superseded, is_active=False) row must be scrubbed from the RAG
    # index — the same cleanup DELETE /rules/{id} performs on its rule_id.
    delete.assert_awaited_once_with(old.id)
    assert old.is_active is False
    # New version still gets indexed.
    upsert.assert_awaited_once()


def test_lifecycle_only_change_does_not_touch_reliability_or_delete_rag():
    # is_active-only toggle stays in place (no new version); must not trigger
    # the content-change RAG-delete cleanup meant for superseded rows.
    old = _old_rule()
    db = _db_returning(old)

    with patch.object(rules_routes, "_safe_rag_upsert", new=AsyncMock()) as upsert, \
         patch.object(rules_routes, "_safe_rag_delete", new=AsyncMock()) as delete:
        asyncio.run(rules_routes.update_rule(
            rule_id=str(old.id), is_active=False, user={}, db=db,
        ))

    delete.assert_not_awaited()
    upsert.assert_awaited_once_with(old.id, db)


def test_scope_change_creates_new_version_and_preserves_old_audit_row():
    old = _old_rule(product_line=None)
    db = _db_returning(old)

    with patch.object(rules_routes, "_safe_rag_upsert", new=AsyncMock()), \
         patch.object(rules_routes, "_safe_rag_delete", new=AsyncMock()):
        asyncio.run(rules_routes.update_rule(
            rule_id=str(old.id), product_line="term", user={}, db=db,
        ))

    new_rule = db.add.call_args[0][0]
    assert new_rule is not old
    assert new_rule.product_line == "term"
    assert old.is_active is False


def test_generated_draft_is_edited_and_approved_in_place():
    evidence_passage_id = str(uuid.uuid4())
    draft = _old_rule(
        is_active=False,
        is_auto_generated=True,
        product_line="global",
        rule_metadata={
            "lifecycle": "draft_pending_review",
            "source_doc_id": str(uuid.uuid4()),
            "source_quote": "Advertisements must disclose all applicable charges.",
            "source_evidence_passage_id": evidence_passage_id,
        },
    )
    db = _db_returning(draft)

    with patch.object(rules_routes, "_safe_rag_upsert", new=AsyncMock()) as upsert, \
         patch.object(rules_routes, "_safe_rag_delete", new=AsyncMock()) as delete, \
         patch.object(rules_routes, "_link_source_evidence_or_fail", new=AsyncMock()) as link:
        result = asyncio.run(rules_routes.update_rule(
            rule_id=str(draft.id),
            rule_text="reviewed text",
            product_line="ulip",
            is_active=True,
            user={},
            db=db,
        ))

    assert db.add.call_count == 0
    assert draft.rule_text == "reviewed text"
    assert draft.product_line == "ulip"
    assert draft.is_active is True
    assert draft.rule_metadata["lifecycle"] == "reviewed_active"
    assert result["id"] == str(draft.id)
    delete.assert_not_awaited()
    upsert.assert_awaited_once_with(draft.id, db)
    link.assert_awaited_once()
    linked_metadata, linked_rule_id = link.await_args.args
    assert linked_metadata["source_evidence_passage_id"] == evidence_passage_id
    assert linked_rule_id == draft.id


def test_generated_draft_without_evidence_cannot_be_approved():
    draft = _old_rule(
        is_active=False,
        is_auto_generated=True,
        product_line="global",
        rule_metadata={
            "lifecycle": "draft_pending_review",
            "source_doc_id": str(uuid.uuid4()),
        },
    )
    db = _db_returning(draft)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(rules_routes.update_rule(
            rule_id=str(draft.id), is_active=True, user={}, db=db,
        ))

    assert exc.value.status_code == 409
    assert draft.is_active is False
    db.commit.assert_not_called()


def test_legacy_unscoped_rule_cannot_be_reactivated():
    old = _old_rule(is_active=False, product_line=None)
    db = _db_returning(old)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(rules_routes.update_rule(
            rule_id=str(old.id), is_active=True, user={}, db=db,
        ))

    assert exc.value.status_code == 400
    assert old.is_active is False
    db.commit.assert_not_called()


@pytest.mark.parametrize("scope", [None, "", "mystery_product"])
def test_scope_validator_rejects_missing_or_unsupported_values(scope):
    with pytest.raises(HTTPException) as exc:
        rules_routes._validated_product_line(scope)

    assert exc.value.status_code == 400


def test_scope_validator_normalizes_supported_values():
    assert rules_routes._validated_product_line(" ULIP ") == "ulip"


@pytest.mark.parametrize("scope", [None, "", "mystery_product"])
def test_rule_service_rejects_missing_or_unsupported_scope(scope):
    db = MagicMock()

    with pytest.raises(ValueError):
        RuleGeneratorService().create_rule(
            db=db,
            category="regulatory",
            rule_text="Disclose charges.",
            product_line=scope,
        )

    db.add.assert_not_called()


def test_active_legacy_content_edit_cannot_create_unscoped_successor():
    old = _old_rule(is_active=True, product_line=None)
    db = _db_returning(old)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(rules_routes.update_rule(
            rule_id=str(old.id), rule_text="new text", user={}, db=db,
        ))

    assert exc.value.status_code == 400
    assert db.add.call_count == 0


def test_inactive_content_edit_stays_inactive():
    old = _old_rule(is_active=False, product_line="term")
    db = _db_returning(old)

    with patch.object(rules_routes, "_safe_rag_upsert", new=AsyncMock()), \
         patch.object(rules_routes, "_safe_rag_delete", new=AsyncMock()):
        asyncio.run(rules_routes.update_rule(
            rule_id=str(old.id), rule_text="new text", user={}, db=db,
        ))

    new_rule = db.add.call_args[0][0]
    assert new_rule.is_active is False


def test_superseded_version_cannot_branch_again():
    old = _old_rule(
        product_line="term",
        superseded_by=uuid.uuid4(),
    )
    db = _db_returning(old)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(rules_routes.update_rule(
            rule_id=str(old.id), rule_text="branch", user={}, db=db,
        ))

    assert exc.value.status_code == 409
    assert db.add.call_count == 0


def test_delete_published_rule_soft_retires_for_provenance():
    old = _old_rule(product_line="term", rule_metadata={})
    db = _db_returning(old)

    with patch.object(rules_routes, "_safe_rag_delete", new=AsyncMock()) as delete:
        result = asyncio.run(rules_routes.delete_rule(
            rule_id=str(old.id), user={}, db=db,
        ))

    assert result["action"] == "retired"
    assert old.is_active is False
    assert old.rule_metadata["lifecycle"] == "retired"
    db.delete.assert_not_called()
    delete.assert_awaited_once_with(str(old.id))


def test_delete_unpublished_generated_draft_removes_it():
    draft = _old_rule(
        is_active=False,
        is_auto_generated=True,
        product_line="term",
        rule_metadata={"lifecycle": "draft_pending_review"},
    )
    db = _db_returning(draft)

    with patch.object(rules_routes, "_safe_rag_delete", new=AsyncMock()):
        result = asyncio.run(rules_routes.delete_rule(
            rule_id=str(draft.id), user={}, db=db,
        ))

    assert result["action"] == "deleted"
    db.delete.assert_called_once_with(draft)
