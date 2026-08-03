"""lexical_state must be nullable: every pre-existing submission has none,
and every code path has to keep working without it."""
from app.models.submission import Submission
from app.models.submission_revision import SubmissionRevision


def test_submission_has_nullable_lexical_columns():
    for name in ("lexical_state", "lexical_html"):
        assert Submission.__table__.columns[name].nullable is True


def test_revision_has_nullable_lexical_columns():
    for name in ("lexical_state", "lexical_html"):
        assert SubmissionRevision.__table__.columns[name].nullable is True
