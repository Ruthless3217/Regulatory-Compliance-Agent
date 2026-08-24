"""GET /submissions/{id}/export/{kind} — dispatch shape (mirrors
GET /comparisons/{id}/export/{kind}): unknown-kind and missing-submission
404s, a GotenbergError mapped to 502, and a successful response's
media-type/filename. Uses the same in-memory FakeSession + direct
route-function-call style as test_submission_revisions_and_comments.py.
"""
import asyncio
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.elements import Null

from app.api.routes import submissions as submissions_routes
from app.models.submission import Submission
from app.services.gotenberg_client import GotenbergError


class _FakeQuery:
    def __init__(self, session, model):
        self._session = session
        self._model = model
        self._predicates = []

    def filter(self, *exprs):
        for e in exprs:
            val = None if isinstance(e.right, Null) else e.right.value
            self._predicates.append((e.left.key, val))
        return self

    def _matches(self, obj):
        for key, val in self._predicates:
            if str(getattr(obj, key, None)) != str(val):
                return False
        return True

    # Ordering/pagination are irrelevant to these dispatch tests — the store
    # holds at most one row per model — but export_common's staleness check
    # chains them, so accept and ignore.
    def order_by(self, *_exprs):
        return self

    def limit(self, *_n):
        return self

    def first(self):
        rows = [o for o in self._session.rows_for(self._model) if self._matches(o)]
        return rows[0] if rows else None

    def scalar(self):
        row = self.first()
        return row if row is None else getattr(row, "created_at", row)


class FakeSession:
    def __init__(self):
        self._store: dict = {}

    def rows_for(self, model):
        return self._store.setdefault(model, [])

    def query(self, model):
        return _FakeQuery(self, model)

    def add(self, obj):
        self.rows_for(type(obj)).append(obj)


class _User:
    # Admin: these tests cover export dispatch, not visibility, and a plain
    # `user` now 404s on a submission that is neither theirs nor assigned.
    def __init__(self, role="admin"):
        self.id = uuid.uuid4()
        self.role = role


def _submission() -> Submission:
    return Submission(id=uuid.uuid4(), title="Fortune Gain II Brochure", content_type="text", original_content="x")


def test_export_404s_on_unknown_kind():
    db = FakeSession()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(submissions_routes.export_submission(
            submission_id=str(uuid.uuid4()), kind="not-a-kind", user=_User(), db=db,
        ))
    assert exc.value.status_code == 404


def test_export_404s_on_missing_submission():
    db = FakeSession()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(submissions_routes.export_submission(
            submission_id=str(uuid.uuid4()), kind="clean.docx", user=_User(), db=db,
        ))
    assert exc.value.status_code == 404


def test_export_maps_gotenberg_error_to_502(monkeypatch):
    sub = _submission()
    db = FakeSession()
    db.add(sub)

    def boom(db_, submission_, kind_):
        raise GotenbergError("sidecar unreachable")

    monkeypatch.setattr(submissions_routes.submission_export_service, "build_export", boom)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(submissions_routes.export_submission(
            submission_id=str(sub.id), kind="clean.pdf", user=_User(), db=db,
        ))
    assert exc.value.status_code == 502


def test_export_returns_the_built_bytes_with_correct_media_type_and_filename(monkeypatch):
    sub = _submission()
    db = FakeSession()
    db.add(sub)

    monkeypatch.setattr(
        submissions_routes.submission_export_service, "build_export",
        lambda db_, submission_, kind_: b"docx-bytes",
    )

    response = asyncio.run(submissions_routes.export_submission(
        submission_id=str(sub.id), kind="clean.docx", user=_User(), db=db,
    ))

    assert response.body == b"docx-bytes"
    assert response.media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert "Fortune-Gain-II-Brochure-clean.docx" in response.headers["content-disposition"]


@pytest.mark.parametrize("kind", list(submissions_routes._EXPORT_MEDIA.keys()))
def test_every_documented_kind_dispatches_without_error(monkeypatch, kind):
    sub = _submission()
    db = FakeSession()
    db.add(sub)
    monkeypatch.setattr(
        submissions_routes.submission_export_service, "build_export",
        lambda db_, submission_, kind_: b"bytes",
    )
    response = asyncio.run(submissions_routes.export_submission(
        submission_id=str(sub.id), kind=kind, user=_User(), db=db,
    ))
    assert response.status_code == 200
