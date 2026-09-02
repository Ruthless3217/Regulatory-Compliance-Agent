"""POST /comparisons/{id}/rerun and /annotations — dispatch shape (mirrors
test_submissions_export_route.py's in-memory FakeSession + direct
route-function-call style, since Compare has no TestClient coverage at all).

Covers the two branches the be-comparison#1 test-gap finding named: the 409
gate in rerun_comparison while a render is still processing, and
upsert_annotation updating an existing row on a second POST with the same
change_id rather than erroring on the unique constraint.
"""
import asyncio
import uuid

import pytest
from fastapi import HTTPException, BackgroundTasks
from sqlalchemy.sql.elements import Null

from app.api.routes import comparisons as comparisons_routes
from app.models.document_comparison import DocumentComparison
from app.models.comparison_annotation import ComparisonAnnotation


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

    def order_by(self, *_exprs):
        return self

    def all(self):
        return [o for o in self._session.rows_for(self._model) if self._matches(o)]

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def delete(self):
        keep = [o for o in self._session.rows_for(self._model) if not self._matches(o)]
        self._session._store[self._model] = keep


class FakeSession:
    def __init__(self):
        self._store: dict = {}

    def rows_for(self, model):
        return self._store.setdefault(model, [])

    def query(self, model):
        return _FakeQuery(self, model)

    def add(self, obj):
        self.rows_for(type(obj)).append(obj)

    def commit(self):
        pass

    def refresh(self, _obj):
        pass


class _User:
    def __init__(self):
        self.id = uuid.uuid4()
        self.role = "admin"


def _comparison(**overrides) -> DocumentComparison:
    defaults = dict(
        id=uuid.uuid4(),
        title="Old Brochure -> New Brochure",
        old_content_type="text",
        new_content_type="text",
        old_original_content="hello world",
        new_original_content="hello there",
        status="completed",
        render_status="skipped",
    )
    defaults.update(overrides)
    return DocumentComparison(**defaults)


def test_rerun_409s_while_render_still_processing():
    c = _comparison(render_status="processing")
    db = FakeSession()
    db.add(c)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(comparisons_routes.rerun_comparison(
            comparison_id=str(c.id),
            background_tasks=BackgroundTasks(),
            swap=False,
            old_content=None,
            new_content=None,
            old_file=None,
            new_file=None,
            user=_User(),
            db=db,
        ))
    assert exc.value.status_code == 409


def test_second_annotation_post_updates_same_row_not_a_duplicate():
    c = _comparison()
    db = FakeSession()
    db.add(c)

    body1 = comparisons_routes.AnnotationIn(change_id="r0", note="first note", tags=["a"])
    asyncio.run(comparisons_routes.upsert_annotation(
        comparison_id=str(c.id), body=body1, user=_User(), db=db,
    ))

    body2 = comparisons_routes.AnnotationIn(change_id="r0", note="second note", tags=["b"])
    asyncio.run(comparisons_routes.upsert_annotation(
        comparison_id=str(c.id), body=body2, user=_User(), db=db,
    ))

    rows = db.rows_for(ComparisonAnnotation)
    assert len(rows) == 1
    assert rows[0].note == "second note"
    assert rows[0].tags == ["b"]
