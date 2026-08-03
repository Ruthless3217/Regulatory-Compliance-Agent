"""The rewrite endpoint proposes wording and must never persist it.

A saved edit invalidates the run's findings and blocks export until a re-run,
so a proposal the reviewer has not accepted yet must not cost them one.
"""
import asyncio
import uuid

import pytest
from fastapi import HTTPException

from app.api.routes import compliance as routes
from app.models.violation import Violation


class _Query:
    def __init__(self, row):
        self._row = row

    def filter(self, *_):
        return self

    def first(self):
        return self._row


class _Db:
    def __init__(self, violation=None, rule=None):
        self._violation = violation
        self._rule = rule
        self.committed = False

    def query(self, model):
        return _Query(self._rule if getattr(model, "__name__", "") == "Rule" else self._violation)

    def commit(self):  # pragma: no cover - must never be reached
        self.committed = True


def _violation(**kw):
    defaults = dict(
        id=uuid.uuid4(),
        description="Implies a guaranteed return.",
        current_text="Guaranteed 12% returns every year.",
        suggested_fix=None,
        regulator_quote=None,
        rule_id=None,
    )
    defaults.update(kw)
    return Violation(**defaults)


def _call(db, violation_id=None, instruction=None):
    return asyncio.run(
        routes.rewrite_violation_text(
            violation_id=str(violation_id or uuid.uuid4()),
            payload=routes.RewriteRequest(instruction=instruction),
            user={"id": uuid.uuid4()},
            db=db,
        )
    )


def test_returns_proposal_without_persisting(monkeypatch):
    async def fake(**_kw):
        return "  \"Returns are not guaranteed and vary with market performance.\"  "

    monkeypatch.setattr(routes.chat_llm_service, "generate_response", fake)
    db = _Db(_violation())
    result = _call(db)

    # Quotes and surrounding whitespace are stripped from the model's answer.
    assert result["proposed_text"] == "Returns are not guaranteed and vary with market performance."
    assert result["original_text"] == "Guaranteed 12% returns every year."
    assert db.committed is False, "rewrite must not write a revision"


def test_reviewer_instruction_reaches_the_prompt(monkeypatch):
    seen = {}

    async def fake(prompt, **_kw):
        seen["prompt"] = prompt
        return "Shorter copy."

    monkeypatch.setattr(routes.chat_llm_service, "generate_response", fake)
    _call(_Db(_violation()), instruction="keep it under 12 words")
    assert "keep it under 12 words" in seen["prompt"]
    assert "Guaranteed 12% returns every year." in seen["prompt"]


def test_missing_violation_is_404():
    with pytest.raises(HTTPException) as exc:
        _call(_Db(None))
    assert exc.value.status_code == 404


def test_finding_without_quoted_text_is_422():
    """Nothing to splice over, so there is nothing to rewrite."""
    with pytest.raises(HTTPException) as exc:
        _call(_Db(_violation(current_text=None)))
    assert exc.value.status_code == 422


def test_empty_model_output_is_502_not_an_empty_rewrite(monkeypatch):
    async def fake(**_kw):
        return "   "

    monkeypatch.setattr(routes.chat_llm_service, "generate_response", fake)
    with pytest.raises(HTTPException) as exc:
        _call(_Db(_violation()))
    assert exc.value.status_code == 502


def test_llm_outage_is_503(monkeypatch):
    async def fake(**_kw):
        raise routes.LLMUnavailableError("no provider")

    monkeypatch.setattr(routes.chat_llm_service, "generate_response", fake)
    with pytest.raises(HTTPException) as exc:
        _call(_Db(_violation()))
    assert exc.value.status_code == 503
