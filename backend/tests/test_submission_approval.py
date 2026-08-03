"""POST /submissions/{id}/approve and GET /submissions/{id}/approval — the
sign-off the workflow was missing.

`submissions.approval_status` was read by two responses and written by no
route, so "approved" was a column nobody could set. These tests pin what
approval has to MEAN before it is worth recording:

* not a superseded document (`export_common.findings_are_stale`),
* not a fragment — "A partial re-run cannot produce a document score, and does
  not pretend to. Approval requires a whole-document run.",
* not an ungraded/degraded submission,
* not one still carrying unaddressed critical findings, unless a human writes
  down why — and that reason lands in the audit record.

Same in-memory fake Session + direct route-function-call style as
test_submission_revisions_and_comments.py / test_submissions_export_route.py
(the Postgres-only UUID/JSONB column types don't survive sqlite).
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.elements import Null

from app.api.routes import submissions as submissions_routes
from app.api.routes.submissions import ApprovalRequest
from app.models.analysis_run import AnalysisRun
from app.models.compliance_check import ComplianceCheck
from app.models.submission import Submission
from app.models.submission_revision import SubmissionRevision
from app.models.violation import Violation

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def audited(monkeypatch):
    """Capture audit events. Also load-bearing as an autouse guard: the real
    audit.record opens a live SessionLocal, so an unpatched call would sit on a
    Postgres connect timeout."""
    events = []

    async def _record(event_type, **kwargs):
        events.append({"event_type": event_type, **kwargs})

    monkeypatch.setattr("app.services.observability.audit.record", _record)
    return events


# ---------------------------------------------------------------------------
# In-memory session
# ---------------------------------------------------------------------------

class _FakeQuery:
    def __init__(self, session, target):
        # query(Model) or query(Model.column) — export_common's staleness check
        # uses the column form (`query(SubmissionRevision.created_at)`).
        self._column = getattr(target, "key", None) if hasattr(target, "class_") else None
        self._model = target.class_ if self._column else target
        self._session = session
        self._predicates = []
        self._order_key = None
        self._order_desc = False

    def filter(self, *exprs):
        for e in exprs:
            val = None if isinstance(e.right, Null) else e.right.value
            self._predicates.append((e.left.key, val))
        return self

    def order_by(self, col):
        self._order_key = getattr(col, "element", col).key
        self._order_desc = hasattr(col, "element")
        return self

    def limit(self, *_n):
        return self

    def _matches(self, obj):
        return all(str(getattr(obj, k, None)) == str(v) for k, v in self._predicates)

    def _rows(self):
        rows = [o for o in self._session.rows_for(self._model) if self._matches(o)]
        if self._order_key:
            vals = [getattr(o, self._order_key, None) for o in rows]
            if all(v is not None for v in vals):
                rows.sort(key=lambda o: getattr(o, self._order_key), reverse=self._order_desc)
        return rows

    def first(self):
        rows = self._rows()
        return rows[0] if rows else None

    def all(self):
        return self._rows()

    def scalar(self):
        row = self.first()
        if row is None:
            return None
        return getattr(row, self._column) if self._column else row


class FakeSession:
    def __init__(self):
        self._store: dict = {}
        self.commits = 0

    def rows_for(self, model):
        return self._store.setdefault(model, [])

    def query(self, target):
        return _FakeQuery(self, target)

    def add(self, obj):
        rows = self.rows_for(type(obj))
        if obj not in rows:
            rows.append(obj)

    def commit(self):
        self.commits += 1

    def refresh(self, obj):
        pass


class _User:
    def __init__(self, role="user"):
        self.id = uuid.uuid4()
        self.role = role


# ---------------------------------------------------------------------------
# Fixtures-by-hand
# ---------------------------------------------------------------------------

def _submission(db, status="analyzed", approval_status="pending") -> Submission:
    sub = Submission(
        id=uuid.uuid4(), title="Fortune Gain II Brochure", content_type="text",
        original_content="orig", status=status, approval_status=approval_status,
    )
    db.add(sub)
    return sub


def _analyzed(db, sub, *, checked_at=NOW, check_status="completed", scoped=False):
    """A completed whole-document analysis: one check + the run that made it."""
    check = ComplianceCheck(
        id=uuid.uuid4(), submission_id=sub.id, checked_at=checked_at,
        overall_score=82.0, grade="B", status=check_status,
    )
    db.add(check)
    meta = {"scoped": True, "scope": {"section_titles": ["Benefits"]}} if scoped else None
    db.add(AnalysisRun(
        id=uuid.uuid4(), submission_id=sub.id, run_number=1, status="completed",
        compliance_check_id=check.id, run_metadata=meta,
    ))
    return check


def _critical(db, check, **overrides):
    v = Violation(
        id=uuid.uuid4(), compliance_check_id=check.id, category="misleading_benefit",
        severity="critical", description="guaranteed returns claim", current_text="guaranteed",
        suppressed=False, source="model", review_status=None, created_at=NOW,
    )
    for k, val in overrides.items():
        setattr(v, k, val)
    db.add(v)
    return v


def _edit(db, sub, at):
    db.add(SubmissionRevision(
        id=uuid.uuid4(), submission_id=sub.id, revision_number=1,
        content="edited", source="manual_edit", created_at=at,
    ))


def _approve(db, sub, user=None, override_reason=None):
    return asyncio.run(submissions_routes.approve_submission(
        submission_id=str(sub.id),
        body=ApprovalRequest(override_reason=override_reason),
        user=user or _User(), db=db,
    ))


def _state(db, sub, user=None):
    return asyncio.run(submissions_routes.get_approval_state(
        submission_id=str(sub.id), user=user or _User(), db=db,
    ))


def _refusal(db, sub, **kw) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        _approve(db, sub, **kw)
    return exc.value


def _codes(state):
    return {b["code"] for b in state["blockers"]}


# ---------------------------------------------------------------------------
# 404s
# ---------------------------------------------------------------------------

def test_approve_404s_for_missing_submission():
    db = FakeSession()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(submissions_routes.approve_submission(
            submission_id=str(uuid.uuid4()), body=ApprovalRequest(), user=_User(), db=db,
        ))
    assert exc.value.status_code == 404


def test_approval_state_404s_for_missing_submission():
    db = FakeSession()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(submissions_routes.get_approval_state(
            submission_id=str(uuid.uuid4()), user=_User(), db=db,
        ))
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 1. Edited since the last analysis — the findings describe a superseded doc
# ---------------------------------------------------------------------------

def test_approve_refuses_a_document_edited_since_the_last_analysis():
    db = FakeSession()
    sub = _submission(db)
    _analyzed(db, sub)
    _edit(db, sub, NOW + timedelta(minutes=5))

    err = _refusal(db, sub)

    assert err.status_code == 409
    assert "edited since the last analysis" in err.detail
    assert sub.approval_status == "pending"


def test_an_edit_before_the_analysis_is_not_stale():
    db = FakeSession()
    sub = _submission(db)
    _analyzed(db, sub)
    _edit(db, sub, NOW - timedelta(minutes=5))

    assert _approve(db, sub)["approval_status"] == "approved"


def test_an_override_reason_does_not_buy_past_a_stale_document():
    # The override is scoped to unaddressed criticals. It is not a skeleton key
    # for the gates that exist because the findings are simply not about this
    # document any more.
    db = FakeSession()
    sub = _submission(db)
    _analyzed(db, sub)
    _edit(db, sub, NOW + timedelta(minutes=5))

    err = _refusal(db, sub, override_reason="signed off verbally by legal")

    assert err.status_code == 409
    assert "edited since the last analysis" in err.detail


# ---------------------------------------------------------------------------
# 2. Scoped / partial latest run
# ---------------------------------------------------------------------------

SCOPED_REFUSAL = (
    "A partial re-run cannot produce a document score, and does not pretend to. "
    "Approval requires a whole-document run."
)


def test_approve_refuses_when_the_latest_run_was_scoped():
    db = FakeSession()
    sub = _submission(db)
    _analyzed(db, sub, scoped=True)

    err = _refusal(db, sub)

    assert err.status_code == 409
    assert SCOPED_REFUSAL in err.detail
    assert sub.approval_status == "pending"


def test_an_override_reason_does_not_buy_past_a_scoped_run():
    db = FakeSession()
    sub = _submission(db)
    _analyzed(db, sub, scoped=True)

    err = _refusal(db, sub, override_reason="only the benefits table changed")

    assert err.status_code == 409
    assert SCOPED_REFUSAL in err.detail


def test_a_full_run_after_a_scoped_one_clears_the_gate():
    # Only the LATEST run decides. An older scoped run must not poison a
    # document that has since been re-analysed whole.
    db = FakeSession()
    sub = _submission(db)
    old = _analyzed(db, sub, checked_at=NOW - timedelta(hours=1))
    db.rows_for(AnalysisRun)[0].run_metadata = {"scoped": True}
    fresh = _analyzed(db, sub, checked_at=NOW)
    db.rows_for(AnalysisRun)[1].run_number = 2

    result = _approve(db, sub)

    assert result["approval_status"] == "approved"
    assert result["check_id"] == str(fresh.id)
    assert old.id != fresh.id


# ---------------------------------------------------------------------------
# 3. No completed analysis / non-gradeable submission
# ---------------------------------------------------------------------------

def test_approve_refuses_a_submission_that_was_never_analysed():
    db = FakeSession()
    sub = _submission(db, status="uploaded")

    err = _refusal(db, sub)

    assert err.status_code == 409
    assert "no completed analysis" in err.detail
    assert sub.approval_status == "pending"


def test_approve_refuses_when_the_latest_check_is_not_completed():
    db = FakeSession()
    sub = _submission(db)
    _analyzed(db, sub, check_status="partial")

    err = _refusal(db, sub)

    assert err.status_code == 409
    assert "no completed analysis" in err.detail


@pytest.mark.parametrize("status", ["needs_review", "failed"])
def test_approve_refuses_a_non_gradeable_submission(status):
    # A degraded/failed run persists NO check, so an older check survives —
    # approving on it would sign off findings the newest attempt could not
    # reproduce.
    db = FakeSession()
    sub = _submission(db, status=status)
    _analyzed(db, sub)

    err = _refusal(db, sub)

    assert err.status_code == 409
    assert status in err.detail
    assert sub.approval_status == "pending"


# ---------------------------------------------------------------------------
# 4. Unresolved criticals, and the explicit override
# ---------------------------------------------------------------------------

def test_approve_refuses_unresolved_criticals_without_a_reason():
    db = FakeSession()
    sub = _submission(db)
    check = _analyzed(db, sub)
    _critical(db, check)

    err = _refusal(db, sub)

    assert err.status_code == 409
    assert "critical" in err.detail
    assert "override_reason" in err.detail
    assert sub.approval_status == "pending"


def test_a_blank_override_reason_is_no_reason_at_all():
    db = FakeSession()
    sub = _submission(db)
    check = _analyzed(db, sub)
    _critical(db, check)

    err = _refusal(db, sub, override_reason="   ")

    assert err.status_code == 409
    assert "override_reason" in err.detail


def test_an_override_reason_approves_and_is_audited(audited):
    db = FakeSession()
    sub = _submission(db)
    check = _analyzed(db, sub)
    _critical(db, check)
    _critical(db, check)
    user = _User()

    result = _approve(db, sub, user=user, override_reason="IRDAI pre-clearance ref 4471")

    assert result["approval_status"] == "approved"
    assert result["overridden"] is True
    assert result["unresolved_critical_count"] == 2
    assert sub.approval_status == "approved"

    event = audited[-1]
    assert event["event_type"] == "submission_approval_override"
    assert event["metadata"]["override_reason"] == "IRDAI pre-clearance ref 4471"
    assert event["metadata"]["unresolved_critical_count"] == 2
    assert event["target_id"] == str(sub.id)


@pytest.mark.parametrize("field,value", [
    ("suppressed", True),         # below the confidence floor — not a live finding
    ("source", "reviewer"),       # reviewer-authored, not a model finding
    ("review_status", "actioned"),  # a human already recorded a verdict
])
def test_a_critical_that_is_not_unresolved_does_not_block(field, value):
    db = FakeSession()
    sub = _submission(db)
    check = _analyzed(db, sub)
    _critical(db, check, **{field: value})

    assert _approve(db, sub)["approval_status"] == "approved"


def test_a_non_critical_finding_does_not_block():
    db = FakeSession()
    sub = _submission(db)
    check = _analyzed(db, sub)
    _critical(db, check, severity="high")

    assert _approve(db, sub)["approval_status"] == "approved"


def test_criticals_of_an_older_check_do_not_block():
    db = FakeSession()
    sub = _submission(db)
    old = _analyzed(db, sub, checked_at=NOW - timedelta(hours=1))
    _critical(db, old)
    _analyzed(db, sub, checked_at=NOW)
    db.rows_for(AnalysisRun)[1].run_number = 2

    assert _approve(db, sub)["approval_status"] == "approved"


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

def test_a_clean_document_approves_and_records_who_and_when(audited):
    db = FakeSession()
    sub = _submission(db)
    check = _analyzed(db, sub)
    user = _User(role="admin")

    result = _approve(db, sub, user=user)

    assert result["approval_status"] == "approved"
    assert result["overridden"] is False
    assert result["check_id"] == str(check.id)
    assert sub.approval_status == "approved"
    assert db.commits >= 1

    # No approved_by/approved_at columns exist on submissions, and this feature
    # ships no migration — so actor + timestamp live in the audit record.
    event = audited[-1]
    assert event["event_type"] == "submission_approved"
    assert event["actor"] is user
    assert event["target_type"] == "submission"
    assert event["target_id"] == str(sub.id)
    assert event["metadata"]["check_id"] == str(check.id)
    assert event["metadata"]["approved_by"] == str(user.id)
    datetime.fromisoformat(event["metadata"]["approved_at"])  # parses, is real
    assert event["metadata"].get("override_reason") is None
    assert event["before"] == {"approval_status": "pending"}
    assert event["after"] == {"approval_status": "approved"}


def test_a_refused_approval_writes_no_audit_event_and_no_status(audited):
    db = FakeSession()
    sub = _submission(db, status="failed")
    _analyzed(db, sub)

    _refusal(db, sub)

    assert audited == []
    assert sub.approval_status == "pending"


# ---------------------------------------------------------------------------
# GET /approval — why the button is disabled
# ---------------------------------------------------------------------------

def test_approval_state_is_clear_for_an_approvable_document():
    db = FakeSession()
    sub = _submission(db)
    check = _analyzed(db, sub)

    state = _state(db, sub)

    assert state["can_approve"] is True
    assert state["requires_override"] is False
    assert state["blockers"] == []
    assert state["approval_status"] == "pending"
    assert state["check_id"] == str(check.id)


def test_approval_state_names_every_blocking_reason():
    db = FakeSession()
    sub = _submission(db, status="needs_review")
    check = _analyzed(db, sub, scoped=True)
    _critical(db, check)
    _edit(db, sub, NOW + timedelta(minutes=5))

    state = _state(db, sub)

    assert state["can_approve"] is False
    assert _codes(state) == {
        "not_gradeable", "stale_findings", "scoped_run", "unresolved_criticals",
    }
    assert all(b["message"] for b in state["blockers"])


def test_approval_state_flags_the_criticals_only_case_as_overridable():
    db = FakeSession()
    sub = _submission(db)
    check = _analyzed(db, sub)
    _critical(db, check)

    state = _state(db, sub)

    assert state["can_approve"] is False
    assert state["requires_override"] is True
    assert _codes(state) == {"unresolved_criticals"}
    assert state["unresolved_critical_count"] == 1
    assert [b["overridable"] for b in state["blockers"]] == [True]


def test_approval_state_does_not_offer_an_override_for_a_hard_blocker():
    db = FakeSession()
    sub = _submission(db)
    check = _analyzed(db, sub, scoped=True)
    _critical(db, check)

    state = _state(db, sub)

    assert state["requires_override"] is False  # the scoped run is not overridable
    assert {b["code"]: b["overridable"] for b in state["blockers"]}["scoped_run"] is False


def test_approval_state_reports_a_never_analysed_document():
    db = FakeSession()
    sub = _submission(db, status="uploaded")

    state = _state(db, sub)

    assert state["can_approve"] is False
    assert _codes(state) == {"no_analysis"}
    assert state["check_id"] is None
