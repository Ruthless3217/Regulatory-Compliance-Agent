"""Source-doc retriever — fetches regulator passages that produced a given
rule, used by chat to quote source material verbatim ("explain rule X").

Access pattern: by_rule(rule_id) — exact lookup via the derived_rule_ids
GIN index.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)


class SourceDocsRetriever:
    def by_rule(self, rule_id: uuid.UUID | str, limit: int = 3) -> List[Dict[str, Any]]:
        """Return passages that produced the given rule, ordered by recency.

        Implemented as a direct SQL call on rag_source_docs since it's a
        single equality lookup with no vector math. Returns [] on the
        Azure backend, which has no equivalent index-based lookup.
        """
        if settings.rag_vector_backend != "pgvector":
            return []
        db: Session = SessionLocal()
        try:
            rows = db.execute(
                text(
                    """
                    SELECT id, document_id, document_title, regulator, chunk_index,
                           page_number, text, uploaded_at
                    FROM rag_source_docs
                    WHERE CAST(:rid AS UUID) = ANY(derived_rule_ids)
                    ORDER BY uploaded_at DESC
                    LIMIT :lim
                    """
                ),
                {"rid": str(rule_id), "lim": limit},
            ).all()
            return [
                {
                    "id": str(r[0]),
                    "document_id": str(r[1]),
                    "document_title": r[2],
                    "regulator": r[3],
                    "chunk_index": r[4],
                    "page_number": r[5],
                    "text": r[6],
                    "uploaded_at": r[7].isoformat() if r[7] else None,
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"by_rule lookup failed: {e}")
            return []
        finally:
            db.close()


_singleton: Optional[SourceDocsRetriever] = None


def get_source_docs_retriever() -> SourceDocsRetriever:
    global _singleton
    if _singleton is None:
        _singleton = SourceDocsRetriever()
    return _singleton
