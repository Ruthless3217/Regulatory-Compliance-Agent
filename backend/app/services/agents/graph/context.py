"""
GraphContext: Thread-local storage for the database session.
Required to pass the DB session into async LangGraph nodes safely.
"""
from contextvars import ContextVar
from sqlalchemy.orm import Session
from typing import Optional

_db_session: ContextVar[Optional[Session]] = ContextVar("_db_session", default=None)


class GraphContext:
    """Context holder for passing the DB session into LangGraph nodes."""

    @staticmethod
    def set_db_session(db: Session):
        """Set the current DB session in context. Returns the token."""
        return _db_session.set(db)

    @staticmethod
    def get_db_session() -> Optional[Session]:
        """Get the current DB session from context."""
        return _db_session.get()
