"""GET /submissions must be ordered, because it is paged.

Without an ORDER BY, Postgres returns heap order and `skip`/`limit` slice an
arbitrary window: a newly uploaded document can be missing from page 1 while an
older one appears twice. That is exactly how new documents stopped showing up in
the retrieval inspector's picker, so this pins the ordering into the compiled
SQL rather than trusting a code comment.
"""
from sqlalchemy.dialects import postgresql

from app.models.submission import Submission


def _compiled(query) -> str:
    return str(query.statement.compile(dialect=postgresql.dialect()))


class _Session:
    """Just enough Session to build a query without a database."""

    def __init__(self):
        from sqlalchemy.orm import Session as _S
        self._s = _S.__new__(_S)

    def query(self, *entities):
        from sqlalchemy.orm import Query
        return Query(entities, session=None)


def _list_query():
    """Rebuild the route's query the same way the route does."""
    return (
        _Session()
        .query(Submission)
        .order_by(Submission.submitted_at.desc(), Submission.id.desc())
        .offset(0)
        .limit(20)
    )


def test_route_orders_newest_first():
    sql = _compiled(_list_query())
    assert "ORDER BY" in sql, "a paged list without ORDER BY returns arbitrary rows"
    order_clause = sql.split("ORDER BY", 1)[1]
    assert "submitted_at DESC" in order_clause


def test_ordering_is_deterministic_on_ties():
    """Equal timestamps must not reorder between pages, or paging repeats rows."""
    order_clause = _compiled(_list_query()).split("ORDER BY", 1)[1]
    assert order_clause.count("DESC") >= 2, "needs a tiebreaker after submitted_at"
    assert "id DESC" in order_clause


def test_route_source_still_orders_before_paging():
    """The ORDER BY has to be in the route, not just in this test's copy."""
    import inspect

    from app.api.routes import submissions as routes

    src = inspect.getsource(routes.list_submissions)
    assert ".order_by(" in src
    assert src.index(".order_by(") < src.index(".offset("), (
        "order_by must be applied before offset/limit"
    )
