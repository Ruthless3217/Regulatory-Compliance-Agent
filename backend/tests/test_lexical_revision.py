"""A saved revision carries the Lexical state, and the submission's working
copy moves with it. `content` (plain text) is still written so findings,
search and the existing exports keep working during Phase 1."""
import asyncio
import uuid

from app.api.routes import submissions as submissions_routes
from app.models.submission import Submission
from app.models.submission_revision import SubmissionRevision
from app.schemas.submission import SubmissionRevisionCreate
# Reuses the in-memory Session stand-in the revision route tests already run
# against — these models use Postgres-only column types that sqlite can't hold.
from tests.test_submission_revisions_and_comments import FakeSession, _User


def test_revision_payload_accepts_state_and_html():
    payload = SubmissionRevisionCreate(
        content="Returns are not guaranteed.",
        source="manual_edit",
        lexical_state={"root": {"children": []}},
        lexical_html="<p>Returns are not guaranteed.</p>",
    )
    assert payload.lexical_state["root"] == {"children": []}
    assert payload.lexical_html.startswith("<p>")


def test_both_lexical_fields_are_optional():
    payload = SubmissionRevisionCreate(content="x", source="manual_edit")
    assert payload.lexical_state is None
    assert payload.lexical_html is None


def _save(db, sub, user, **kwargs):
    return asyncio.run(submissions_routes.create_revision(
        submission_id=str(sub.id),
        body=SubmissionRevisionCreate(**kwargs),
        user=user, db=db,
    ))


def test_saving_a_state_writes_it_to_both_the_revision_and_the_submission():
    db, user = FakeSession(), _User()
    sub = Submission(id=uuid.uuid4(), title="t", content_type="docx", original_content="orig")
    db.add(sub)

    state = {"root": {"children": []}}
    _save(db, sub, user, content="edited", source="manual_edit",
          lexical_state=state, lexical_html="<p>edited</p>")

    revision = db.rows_for(SubmissionRevision)[0]
    assert revision.lexical_state == state
    assert revision.lexical_html == "<p>edited</p>"
    # The submission's working copy moves with the revision, or the next open
    # would rehydrate the editor from a stale document.
    assert sub.lexical_state == state
    assert sub.lexical_html == "<p>edited</p>"


def test_a_text_only_save_cannot_blank_an_existing_working_document():
    db, user = FakeSession(), _User()
    sub = Submission(id=uuid.uuid4(), title="t", content_type="docx",
                     lexical_state={"root": {"children": []}}, lexical_html="<p>kept</p>")
    db.add(sub)

    _save(db, sub, user, content="fix applied", source="apply_fix")

    assert sub.lexical_state == {"root": {"children": []}}
    assert sub.lexical_html == "<p>kept</p>"
