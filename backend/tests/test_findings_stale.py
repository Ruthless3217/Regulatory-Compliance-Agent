"""An export must never pair a corrected document with pre-correction findings.

`export_common.findings_are_stale` derives that from two timestamps rather than
a stored flag, so these cover the boundary and the two NULL cases that would
otherwise block every legacy export.
"""
import uuid
from datetime import datetime, timedelta, timezone

from app.models.compliance_check import ComplianceCheck
from app.models.submission_revision import SubmissionRevision
from app.services import export_common


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_):
        return self

    def order_by(self, *_):
        return self

    def limit(self, *_):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0] if self._rows else None


class _Db:
    """Routes the check query and the revision-timestamp query separately."""

    def __init__(self, check, edit_times):
        self._check = check
        self._edits = sorted(edit_times, reverse=True)

    def query(self, target):
        if target is ComplianceCheck:
            return _Query([self._check] if self._check else [])
        assert target is SubmissionRevision.created_at, target
        return _Query(self._edits)


def _check(at):
    return ComplianceCheck(id=uuid.uuid4(), submission_id=uuid.uuid4(), checked_at=at)


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
SUB = uuid.uuid4()


def test_edit_after_analysis_is_stale():
    db = _Db(_check(NOW), [NOW + timedelta(seconds=1)])
    assert export_common.findings_are_stale(db, SUB) is True


def test_edit_before_analysis_is_not_stale():
    """The normal post-re-run state: analysis is newer than the last edit."""
    db = _Db(_check(NOW), [NOW - timedelta(minutes=5)])
    assert export_common.findings_are_stale(db, SUB) is False


def test_simultaneous_is_not_stale():
    """Strictly-greater boundary: equal timestamps must not block an export."""
    db = _Db(_check(NOW), [NOW])
    assert export_common.findings_are_stale(db, SUB) is False


def test_never_edited_is_not_stale():
    assert export_common.findings_are_stale(_Db(_check(NOW), []), SUB) is False


def test_never_analysed_is_not_stale():
    """Nothing to contradict yet, so editing a never-graded document is fine."""
    assert export_common.findings_are_stale(_Db(None, [NOW]), SUB) is False


def test_null_timestamps_do_not_block_legacy_rows():
    assert export_common.findings_are_stale(_Db(_check(None), [NOW]), SUB) is False
    assert export_common.findings_are_stale(_Db(_check(NOW), [None]), SUB) is False
