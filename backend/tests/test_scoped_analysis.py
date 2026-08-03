"""POST /compliance/analyze/{id}/scoped and GET /compliance/submissions/{id}/scopes.

A reviewer who edited two paragraphs re-checks those two paragraphs. The hour of
review they already spent on the rest of the document is not currency to pay for
it, so this suite pins the three load-bearing guarantees:

1. Findings OUTSIDE the requested scope survive the run as the SAME rows —
   same id (reviewer verdicts in rule_feedback join on it), same review_status.
2. A partial run writes no overall_score and no grade. "A partial re-run cannot
   produce a document score, and does not pretend to."
3. The run itself records scoped=true + its scope, so a later reader can never
   read the check as a whole-document grade.

Uses the in-memory fake Session the sibling suites use (Postgres-only
UUID/JSONB column types don't survive sqlite) and calls the route functions
directly.
"""
import asyncio
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.elements import Null

from app.api.routes import compliance as compliance_routes
from app.api.routes.compliance import ScopedAnalyzeRequest
from app.models.analysis_run import AnalysisRun
from app.models.compliance_check import ComplianceCheck
from app.models.submission import Submission
from app.models.violation import Violation


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    """The route fires a fire-and-forget audit event; audit.record opens a REAL
    SessionLocal, which would sit on a Postgres connect timeout."""
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("app.services.observability.audit.record", _noop)


# ---------------------------------------------------------------------------
# In-memory session
# ---------------------------------------------------------------------------

class _FakeQuery:
    def __init__(self, session, model):
        self._session = session
        self._model = model
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

    def _matches(self, obj):
        return all(str(getattr(obj, k, None)) == str(v) for k, v in self._predicates)

    def _rows(self):
        rows = [o for o in self._session.rows_for(self._model) if self._matches(o)]
        if self._order_key:
            rows.sort(key=lambda o: getattr(o, self._order_key, None) or 0, reverse=self._order_desc)
        return rows

    def first(self):
        rows = self._rows()
        return rows[0] if rows else None

    def all(self):
        return self._rows()


class FakeSession:
    def __init__(self):
        self._store: dict = {}
        self.commits = 0

    def rows_for(self, model):
        return self._store.setdefault(model, [])

    def query(self, model):
        return _FakeQuery(self, model)

    def add(self, obj):
        rows = self.rows_for(type(obj))
        if obj not in rows:
            rows.append(obj)

    def delete(self, obj):
        rows = self.rows_for(type(obj))
        if obj in rows:
            rows.remove(obj)

    def commit(self):
        self.commits += 1

    def refresh(self, obj):
        pass


class _User:
    def __init__(self, role="user"):
        self.id = uuid.uuid4()
        self.role = role


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _submission(db) -> Submission:
    s = Submission(id=uuid.uuid4(), title="t", content_type="text",
                   original_content="orig", status="analyzed")
    db.add(s)
    return s


def _check(db, submission, checked_at=1, score=78.0, grade="C") -> ComplianceCheck:
    c = ComplianceCheck(id=uuid.uuid4(), submission_id=submission.id, checked_at=checked_at,
                        overall_score=score, grade=grade, status="completed",
                        scores={"overall": score})
    db.add(c)
    return c


def _violation(db, check, section_title=None, chunk_index=None, description="d",
               review_status=None) -> Violation:
    v = Violation(
        id=uuid.uuid4(), compliance_check_id=check.id, category="irdai",
        severity="high", description=description, section_title=section_title,
        chunk_index=chunk_index, source="model", review_status=review_status,
    )
    db.add(v)
    return v


def _on_check(db, check) -> list:
    """The findings a reader of that check sees — i.e. what /results returns."""
    return [v for v in db.rows_for(Violation)
            if str(v.compliance_check_id) == str(check.id)]


def _fake_engine(db, submission, findings, run_number=2, returns_check=True,
                 run_metadata=None):
    """Stand-in for ComplianceEngine.analyze_submission.

    Mirrors what the real engine persists: a fresh scored ComplianceCheck, one
    Violation per finding, an AnalysisRun pointing at the check, and the
    submission flipped to 'analyzed'. `findings` is [(section_title, chunk_index)].
    """
    created = {}

    async def _analyze(submission_id, session, user=None, session_id=None):
        created["called"] = True
        if not returns_check:
            submission.status = "needs_review"
            return None
        check = ComplianceCheck(
            id=uuid.uuid4(), submission_id=submission.id, checked_at=99,
            overall_score=91.5, grade="A", status="completed",
            scores={"overall": 91.5, "grade": "A"},
        )
        session.add(check)
        run = AnalysisRun(
            id=uuid.uuid4(), submission_id=submission.id, compliance_check_id=check.id,
            run_number=run_number, status="completed",
            run_metadata=run_metadata if run_metadata is not None else {"grounding_mix": {"rule": 3}},
        )
        session.add(run)
        for title, idx in findings:
            session.add(Violation(
                id=uuid.uuid4(), compliance_check_id=check.id, category="irdai",
                severity="high", description=f"fresh finding for {title!r}/{idx}",
                section_title=title, chunk_index=idx, source="model",
                analysis_run_id=run.id,
            ))
        submission.status = "analyzed"
        created["check"] = check
        created["run"] = run
        return check

    return _analyze, created


def _scoped(db, submission_id, user=None, **scope):
    return asyncio.run(compliance_routes.analyze_submission_scoped(
        submission_id=str(submission_id),
        payload=ScopedAnalyzeRequest(**scope),
        request=None,
        user=user or _User(),
        db=db,
    ))


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------

def test_scoped_run_with_no_scope_is_400(monkeypatch):
    """A scoped run with no scope IS a full run, and must be asked for as one."""
    db = FakeSession()
    sub = _submission(db)
    _check(db, sub)
    engine, created = _fake_engine(db, sub, [])
    monkeypatch.setattr(compliance_routes.ComplianceEngine, "analyze_submission", engine)

    for scope in ({}, {"section_titles": []}, {"chunk_indexes": []},
                  {"section_titles": [], "chunk_indexes": []}):
        with pytest.raises(HTTPException) as exc:
            _scoped(db, sub.id, **scope)
        assert exc.value.status_code == 400
        assert "scope" in exc.value.detail.lower()
    # And it never burned an LLM run on the way to rejecting.
    assert "called" not in created


def test_unknown_submission_is_404(monkeypatch):
    db = FakeSession()
    engine, created = _fake_engine(db, _submission(db), [])
    monkeypatch.setattr(compliance_routes.ComplianceEngine, "analyze_submission", engine)
    with pytest.raises(HTTPException) as exc:
        _scoped(db, uuid.uuid4(), section_titles=["Benefits"])
    assert exc.value.status_code == 404
    assert "called" not in created


def test_never_analysed_submission_is_400(monkeypatch):
    """Nothing to re-scope: there are no prior findings and no prior verdicts."""
    db = FakeSession()
    sub = _submission(db)
    engine, created = _fake_engine(db, sub, [])
    monkeypatch.setattr(compliance_routes.ComplianceEngine, "analyze_submission", engine)
    with pytest.raises(HTTPException) as exc:
        _scoped(db, sub.id, section_titles=["Benefits"])
    assert exc.value.status_code == 400
    assert "called" not in created


# ---------------------------------------------------------------------------
# The guarantee: out-of-scope review survives
# ---------------------------------------------------------------------------

def test_out_of_scope_findings_and_their_verdicts_survive(monkeypatch):
    db = FakeSession()
    sub = _submission(db)
    prior = _check(db, sub, checked_at=1)
    reviewed = _violation(db, prior, section_title="Exclusions", chunk_index=7,
                          description="reviewed hours ago", review_status="actioned")
    reviewed_id = reviewed.id
    stale = _violation(db, prior, section_title="Benefits", chunk_index=2,
                       description="about to be replaced")

    engine, created = _fake_engine(db, sub, [("Benefits", 2), ("Exclusions", 7)])
    monkeypatch.setattr(compliance_routes.ComplianceEngine, "analyze_submission", engine)

    out = _scoped(db, sub.id, section_titles=["Benefits"])

    current = _on_check(db, created["check"])

    # The reviewed finding is the SAME ROW — same id, so every rule_feedback
    # verdict keyed on it still resolves — with its review_status intact, and
    # it is still what the reviewer sees on the current check.
    assert reviewed in current
    assert reviewed.id == reviewed_id
    assert reviewed.review_status == "actioned"
    assert reviewed.description == "reviewed hours ago"

    # The re-run did NOT keep its own fresh copy of the out-of-scope finding —
    # that copy would have been the reviewed row's replacement, verdict lost.
    assert [v for v in current if v.section_title == "Exclusions"] == [reviewed]

    # The in-scope finding WAS replaced by the new run's version.
    in_scope_now = [v for v in current if v.section_title == "Benefits"]
    assert len(in_scope_now) == 1
    assert in_scope_now[0].description.startswith("fresh finding")
    assert stale not in current
    # The superseded row is not destroyed: it stays on the check that produced
    # it, as the record of what this scoped run replaced.
    assert _on_check(db, prior) == [stale]

    assert out["replaced_count"] == 1
    assert out["preserved_count"] == 1


def test_scope_can_be_given_as_chunk_indexes(monkeypatch):
    db = FakeSession()
    sub = _submission(db)
    prior = _check(db, sub, checked_at=1)
    kept = _violation(db, prior, section_title="Exclusions", chunk_index=7,
                      review_status="actioned")
    _violation(db, prior, section_title="Benefits", chunk_index=2)

    engine, created = _fake_engine(db, sub, [("Benefits", 2), ("Exclusions", 7)])
    monkeypatch.setattr(compliance_routes.ComplianceEngine, "analyze_submission", engine)

    _scoped(db, sub.id, chunk_indexes=[2])

    current = _on_check(db, created["check"])
    assert [v for v in current if v.chunk_index == 7] == [kept]
    assert kept.review_status == "actioned"
    assert [v for v in current if v.chunk_index == 2][0].description.startswith("fresh")


def test_a_degraded_run_changes_nothing(monkeypatch):
    """The engine refused to grade (needs_review/failed). Nothing was replaced,
    so nothing may be discarded either."""
    db = FakeSession()
    sub = _submission(db)
    prior = _check(db, sub, checked_at=1)
    before = [_violation(db, prior, section_title="Benefits", chunk_index=2,
                         review_status="actioned")]

    engine, _ = _fake_engine(db, sub, [], returns_check=False)
    monkeypatch.setattr(compliance_routes.ComplianceEngine, "analyze_submission", engine)

    out = _scoped(db, sub.id, section_titles=["Benefits"])

    assert out["status"] == "needs_review"
    assert db.rows_for(Violation) == before
    assert before[0].review_status == "actioned"
    assert prior.overall_score == 78.0  # the prior grade is not vandalised


# ---------------------------------------------------------------------------
# The guarantee: a partial run is not a grade
# ---------------------------------------------------------------------------

def test_scoped_run_produces_no_document_score_or_grade(monkeypatch):
    db = FakeSession()
    sub = _submission(db)
    prior = _check(db, sub, checked_at=1)
    _violation(db, prior, section_title="Benefits", chunk_index=2)

    engine, created = _fake_engine(db, sub, [("Benefits", 2)])
    monkeypatch.setattr(compliance_routes.ComplianceEngine, "analyze_submission", engine)

    out = _scoped(db, sub.id, section_titles=["Benefits"])

    check = created["check"]
    assert check.overall_score is None
    assert check.grade is None
    assert out["overall_score"] is None
    assert out["grade"] is None
    # The earlier whole-document grade is left alone on its own check.
    assert prior.overall_score == 78.0
    assert prior.grade == "C"


def test_run_records_scoped_true_and_its_scope(monkeypatch):
    db = FakeSession()
    sub = _submission(db)
    prior = _check(db, sub, checked_at=1)
    _violation(db, prior, section_title="Benefits", chunk_index=2)

    engine, created = _fake_engine(db, sub, [("Benefits", 2)])
    monkeypatch.setattr(compliance_routes.ComplianceEngine, "analyze_submission", engine)

    _scoped(db, sub.id, section_titles=["Benefits"], chunk_indexes=[2, 3])

    md = created["run"].run_metadata
    assert md["scoped"] is True
    assert md["scope"] == {"section_titles": ["Benefits"], "chunk_indexes": [2, 3]}
    # Whatever observability the engine already wrote is preserved, not clobbered.
    assert md["grounding_mix"] == {"rule": 3}


def test_run_history_surfaces_the_partial_marker(monkeypatch):
    """The reviewer-facing run list must show it too — a marker nobody can read
    is not a marker."""
    db = FakeSession()
    sub = _submission(db)
    prior = _check(db, sub, checked_at=1)
    _violation(db, prior, section_title="Benefits", chunk_index=2)
    db.add(AnalysisRun(id=uuid.uuid4(), submission_id=sub.id,
                       compliance_check_id=prior.id, run_number=1, status="completed"))

    engine, created = _fake_engine(db, sub, [("Benefits", 2)])
    monkeypatch.setattr(compliance_routes.ComplianceEngine, "analyze_submission", engine)
    _scoped(db, sub.id, section_titles=["Benefits"])

    listed = asyncio.run(compliance_routes.list_submission_runs(
        submission_id=str(sub.id), user=_User(), db=db))
    by_number = {r["run_number"]: r for r in listed["runs"]}
    assert by_number[1]["scoped"] is False
    assert by_number[2]["scoped"] is True
    assert by_number[2]["scope"] == {"section_titles": ["Benefits"], "chunk_indexes": None}


# ---------------------------------------------------------------------------
# GET /scopes — real scopes for the UI instead of free text
# ---------------------------------------------------------------------------

def _scopes(db, submission_id):
    return asyncio.run(compliance_routes.list_submission_scopes(
        submission_id=str(submission_id), user=_User(), db=db))


def test_scopes_lists_distinct_section_titles_with_counts():
    db = FakeSession()
    sub = _submission(db)
    old = _check(db, sub, checked_at=1)
    _violation(db, old, section_title="Gone")           # older check: not listed
    latest = _check(db, sub, checked_at=2)
    _violation(db, latest, section_title="Benefits")
    _violation(db, latest, section_title="Benefits")
    _violation(db, latest, section_title="Exclusions")
    _violation(db, latest, section_title=None)          # untargetable by title

    out = _scopes(db, sub.id)

    assert out["scopes"] == [
        {"section_title": "Benefits", "count": 2},
        {"section_title": "Exclusions", "count": 1},
    ]
    assert out["untitled_count"] == 1


def test_scopes_is_404_for_unknown_submission():
    db = FakeSession()
    with pytest.raises(HTTPException) as exc:
        _scopes(db, uuid.uuid4())
    assert exc.value.status_code == 404


def test_scopes_is_empty_when_never_analysed():
    db = FakeSession()
    sub = _submission(db)
    assert _scopes(db, sub.id)["scopes"] == []
