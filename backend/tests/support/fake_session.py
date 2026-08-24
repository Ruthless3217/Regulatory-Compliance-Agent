"""In-memory stand-in for a SQLAlchemy Session.

The suite cannot use sqlite: the models lean on Postgres-only column types
(`UUID`, `JSONB`, `ARRAY`) that sqlite will not create. Route functions are
therefore called directly with this object in place of `db`.

Supports only what the routes actually do — equality filters, ordering,
first/all/count/scalar. Anything richer (``or_``, ``in_``) is deliberately
unsupported: a route needing it should be tested through its own pure
predicate instead of by growing this shim into a query engine.
"""
from sqlalchemy.sql.elements import Null


class FakeQuery:
    def __init__(self, session, target):
        # query(Model) or query(Model.column) — the column form is used by
        # export_common's staleness check.
        self._column = getattr(target, "key", None) if hasattr(target, "class_") else None
        self._model = target.class_ if self._column else target
        self._session = session
        self._predicates = []
        self._order_key = None
        self._order_desc = False
        self._limit = None

    def filter(self, *exprs):
        for e in exprs:
            val = None if isinstance(e.right, Null) else e.right.value
            self._predicates.append((e.left.key, val))
        return self

    # SQLAlchemy's keyword form, used by a few call sites.
    def filter_by(self, **kwargs):
        self._predicates.extend(kwargs.items())
        return self

    def order_by(self, col):
        # `Column.desc()` wraps the column in a UnaryExpression — unwrap it and
        # remember the direction.
        self._order_key = getattr(col, "element", col).key
        self._order_desc = hasattr(col, "element")
        return self

    def limit(self, n=None):
        self._limit = n
        return self

    def offset(self, _n=None):
        return self

    def _matches(self, obj):
        return all(str(getattr(obj, k, None)) == str(v) for k, v in self._predicates)

    def _rows(self):
        rows = [o for o in self._session.rows_for(self._model) if self._matches(o)]
        if self._order_key:
            vals = [getattr(o, self._order_key, None) for o in rows]
            if all(v is not None for v in vals):
                rows.sort(key=lambda o: getattr(o, self._order_key), reverse=self._order_desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def first(self):
        rows = self._rows()
        return rows[0] if rows else None

    def all(self):
        return self._rows()

    def count(self):
        return len(self._rows())

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
        return FakeQuery(self, target)

    def add(self, obj):
        rows = self.rows_for(type(obj))
        if obj not in rows:
            rows.append(obj)

    def delete(self, obj):
        rows = self.rows_for(type(obj))
        if obj in rows:
            rows.remove(obj)

    def get(self, model, pk):
        return self.query(model).filter(model.id == pk).first()

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def refresh(self, _obj):
        pass

    def flush(self):
        pass
