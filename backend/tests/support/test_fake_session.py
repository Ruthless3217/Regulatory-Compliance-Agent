"""The shared in-memory Session stand-in. Extracted from the copies in
test_submission_approval.py et al so new suites stop cloning it."""
import uuid

from app.models.submission import Submission
from tests.support.fake_session import FakeSession


def test_add_then_query_by_equality():
    db = FakeSession()
    sub = Submission(id=uuid.uuid4(), title="Brochure", content_type="docx")
    db.add(sub)

    found = db.query(Submission).filter(Submission.id == sub.id).first()

    assert found is sub


def test_query_returns_none_when_no_match():
    db = FakeSession()
    assert db.query(Submission).filter(Submission.id == uuid.uuid4()).first() is None


def test_commit_is_counted():
    db = FakeSession()
    db.commit()
    db.commit()
    assert db.commits == 2


def test_order_by_desc_sorts_descending():
    db = FakeSession()
    a = Submission(id=uuid.uuid4(), title="a", content_type="text", submitted_at=1)
    b = Submission(id=uuid.uuid4(), title="b", content_type="text", submitted_at=2)
    db.add(a)
    db.add(b)

    rows = db.query(Submission).order_by(Submission.submitted_at.desc()).all()

    assert [r.title for r in rows] == ["b", "a"]


def test_delete_removes_the_row():
    db = FakeSession()
    sub = Submission(id=uuid.uuid4(), title="gone", content_type="text")
    db.add(sub)
    db.delete(sub)
    assert db.query(Submission).filter(Submission.id == sub.id).first() is None
