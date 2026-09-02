"""GET /dashboard/summary's recent_submissions used to be company-wide —
`db.query(Submission)...limit(5)` with no visibility scoping — so a plain
'user' (who holds dashboard:view like everyone else) could see the title and
status of any document, bypassing the visibility model the rest of the app
enforces (app/auth/visibility.py). The route now applies
visible_submission_filter(user), same None-means-unrestricted convention as
the submissions list route.

visible_submission_filter builds an or_/in_ clause that needs a real
database (see its docstring) — the query is instead built and *compiled*
here, the same technique test_submission_list_ordering.py uses, so this pins
the actual WHERE clause without needing Postgres.
"""
import uuid

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Query, Session as _S

from app.auth.visibility import visible_submission_filter
from app.models.submission import Submission


class _User:
    def __init__(self, role):
        self.id = uuid.uuid4()
        self.role = role


def _compiled(query) -> str:
    return str(query.statement.compile(dialect=postgresql.dialect()))


def _recent_submissions_query(user):
    """Rebuild the route's query exactly as dashboard.py does."""
    session = _S.__new__(_S)
    q = Query(Submission, session=None)
    scope = visible_submission_filter(user)
    if scope is not None:
        q = q.filter(scope)
    return q.order_by(Submission.submitted_at.desc()).limit(5)


def test_admin_gets_no_visibility_where_clause():
    sql = _compiled(_recent_submissions_query(_User("admin")))
    assert "WHERE" not in sql


def test_super_admin_gets_no_visibility_where_clause():
    sql = _compiled(_recent_submissions_query(_User("super_admin")))
    assert "WHERE" not in sql


def test_a_plain_user_gets_scoped_to_their_own_submissions_and_assignments():
    sql = _compiled(_recent_submissions_query(_User("user")))
    assert "WHERE" in sql
    assert "submissions.submitted_by =" in sql
    assert "review_assignments" in sql
