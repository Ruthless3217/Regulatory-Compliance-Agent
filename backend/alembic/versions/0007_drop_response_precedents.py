"""rag_compliance_examples — purge pure-response precedents (reviewer-voice design).

Some reviewer comments in the corpus are pure responses ("done", "ok", "added"),
not compliance flags. They carry no signal yet still surface in retrieval and
dilute precision. This migration deletes them, logging each deleted row into a
`_purged_response_precedents` audit table so downgrade is fully reversible.

Conservative by design: only the exact response-token denylist is removed.
Substantive short flags ("source?", "rephrase.", "pls add source") are kept.
Expected impact ~600-800 rows (12-15% of the 5,323-row corpus).

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-28
"""
import re
from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Canonical response-token denylist. Mirrors precedent_retriever._RESPONSE_TOKENS
# (kept in sync by test_alembic_0007_predicate_matches_runtime_guard). Frozen as
# a migration artifact — do not import the app constant here, migrations must be
# self-contained against future refactors.
RESPONSE_DENYLIST = frozenset({
    "done", "ok", "okay", "yes", "no",
    "noted", "agreed", "agree", "fine", "accepted", "approved", "confirmed",
    "added", "edited", "deleted", "revised", "rephrased", "checked", "check",
})

# SQL normalization applied per row before denylist comparison. The Python
# mirror (_is_purged) must stay behaviourally identical.
_NORM = "lower(regexp_replace(trim(comment_text), '[.!?]+$', ''))"

_AUDIT = "_purged_response_precedents"


def _is_purged(comment_text: str) -> bool:
    """Python mirror of the SQL purge predicate — used by tests and as the
    canonical reference for what the migration deletes."""
    normalized = re.sub(r"[.!?]+$", "", (comment_text or "").strip()).lower()
    return normalized in RESPONSE_DENYLIST


def _denylist_array_sql() -> str:
    # Tokens are simple lowercase words — safe to inline as SQL string literals.
    return "ARRAY[" + ", ".join("'%s'" % t for t in sorted(RESPONSE_DENYLIST)) + "]"


def upgrade() -> None:
    arr = _denylist_array_sql()
    # 1. Snapshot the doomed rows (full row, incl. embedding) into the audit
    #    table so downgrade can restore them verbatim.
    op.execute(
        f"CREATE TABLE IF NOT EXISTS {_AUDIT} AS "
        f"SELECT * FROM rag_compliance_examples WHERE {_NORM} = ANY({arr})"
    )
    # 2. Delete them from the live corpus.
    op.execute(
        f"DELETE FROM rag_compliance_examples WHERE {_NORM} = ANY({arr})"
    )


def downgrade() -> None:
    # Restore the purged rows (the tsv trigger regenerates search_tsv on insert)
    # and drop the audit table. ON CONFLICT guards against partial re-runs.
    op.execute(
        f"INSERT INTO rag_compliance_examples "
        f"SELECT * FROM {_AUDIT} ON CONFLICT (id) DO NOTHING"
    )
    op.execute(f"DROP TABLE IF EXISTS {_AUDIT}")
