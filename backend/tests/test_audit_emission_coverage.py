"""Mutations that must leave a trace.

Editing a document, commenting on it, and exporting it all changed state while
recording nothing. For a compliance trail an unrecorded change is the failure
mode that matters: the trail reads as complete while being wrong.

Asserted against the handler source rather than by driving each route, so a
handler that later drops its audit call fails here even if its own test still
passes.
"""
import pathlib

ROUTES = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "routes"


def _handler_body(src: str, decorator: str) -> str:
    start = src.index(decorator)
    nxt = src.find("\n@router.", start + 1)
    return src[start: nxt if nxt != -1 else len(src)]


def test_document_edits_are_recorded():
    src = (ROUTES / "submissions.py").read_text(encoding="utf-8")
    body = _handler_body(src, '@router.post("/{submission_id}/revisions")')
    assert "submission_edited" in body
    # record_sync, not the fire-and-forget record: an edit whose audit row
    # vanished is an edit nobody can attribute.
    assert "record_sync" in body


def test_edit_events_carry_the_revision_number():
    """Without it the trail can show that an edit happened but never what it
    changed — trail_service resolves the diff through this number."""
    src = (ROUTES / "submissions.py").read_text(encoding="utf-8")
    body = _handler_body(src, '@router.post("/{submission_id}/revisions")')
    assert "revision_number" in body


def test_comment_routes_are_recorded():
    src = (ROUTES / "submissions.py").read_text(encoding="utf-8")
    for decorator, event in (
        ('@router.post("/{submission_id}/comments")', "comment_created"),
        ('@router.patch("/{submission_id}/comments/{comment_id}")', "comment_updated"),
        ('@router.delete("/{submission_id}/comments/{comment_id}")', "comment_deleted"),
    ):
        assert event in _handler_body(src, decorator), f"{event} not emitted"


def test_export_is_recorded():
    src = (ROUTES / "submissions.py").read_text(encoding="utf-8")
    body = _handler_body(src, '@router.get("/{submission_id}/export/{kind}")')
    assert "export_generated" in body


def test_violation_actions_carry_the_document_scope():
    """A violation event belongs to its document's trail, which is what
    scope_submission_id is for."""
    src = (ROUTES / "compliance.py").read_text(encoding="utf-8")
    body = _handler_body(src, '@router.post("/violations/{violation_id}/actions")')
    assert "scope_submission_id" in body


def test_reviewer_authored_violations_carry_the_document_scope():
    src = (ROUTES / "compliance.py").read_text(encoding="utf-8")
    body = _handler_body(src, '@router.post("/submissions/{submission_id}/violations"')
    assert "scope_submission_id" in body
