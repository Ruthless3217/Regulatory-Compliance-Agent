"""rules — deactivate mis-sourced auto-generated rules (KB/rules audit 2026-06-02).

PROBLEM
    The corpus ingest distilled "rules" from content that is NOT a source of
    binding regulatory rules: one product's marketing leaflet (smart-secure),
    a marketing blog article (17641 "5 Financial Gifts…"), and a test artifact
    (E2E rule-gen re-verify). Stored as active rules, they false-flag unrelated
    submissions (e.g. "Entry Age 18-65", "PPF limited to Rs 1.5 lakhs") or
    encode the *opposite* of compliance guidance ("Child insurance plans must
    combine insurance and investment").

WHAT THIS DOES (reversible, audit-table backed — mirrors 0007)
    1. RE-SOURCE the 4 genuine regulatory rules that happen to live in the
       smart-secure leaflet (Section 45 fraud, Section 41 rebates, the 105%
       minimum-death-benefit floor, the 30-day free-look) to a clean
       regulatory source so they are NOT treated as mis-sourced.
    2. SNAPSHOT every remaining wrong-source auto-gen rule into the audit table
       `_deactivated_missourced_rules` (full row, for verbatim restore).
    3. Set `is_active = false` on those rules in BOTH the `rules` table and the
       `rag_rules` vector index (analysis reads rules from rag_rules filtered to
       is_active=true; both stores must agree). Rows are kept, only deactivated.

    Expected impact: 22 rules deactivated (10 blog + 3 test + 9 product-spec),
    4 re-sourced. Nothing is hard-deleted; downgrade fully restores.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-03
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Wrong-source markers (case-insensitive substring, matched against
# generation_source and rule_metadata->>'source'). Frozen copy of the
# export_kill_list reviewer markers — migrations stay self-contained.
KILL_MARKERS: Sequence[str] = (
    "marketing", "leaflet", "brochure", "smart-secure", "smart secure",
    "financial gift", "17641", "e2e", "re-verify", "rule-gen", "test",
    "sample", "draft", "product",
)

# Genuine regulatory rules wrongly sourced from the smart-secure leaflet.
# Matched by a stable, apostrophe-free substring of rule_text → re-sourced to a
# clean regulatory citation so the kill predicate below spares them. Maps
# substring → new source.
KEEPERS = {
    "105% of total premiums paid": "irdai-ulip-min-death-benefit",
    "section 45 of insurance act 1938": "insurance-act-1938-s45",
    "free look period of 30 days": "irdai-free-look-period",
    "rebates except as permitted": "insurance-act-1938-s41",
}

# Original source of all 4 keepers — restored on downgrade.
_KEEPER_ORIGINAL_SOURCE = "smart-secure-leaflet"

_AUDIT = "_deactivated_missourced_rules"


# --- Python mirrors of the SQL predicates (canonical reference for tests) ----

def _matches_kill_marker(generation_source, meta_source) -> bool:
    blob = f"{generation_source or ''} {meta_source or ''}".lower()
    return any(m in blob for m in KILL_MARKERS)


def _is_keeper(rule_text) -> bool:
    t = (rule_text or "").lower()
    return any(sub in t for sub in KEEPERS)


# --- SQL builders ------------------------------------------------------------

def _markers_array_sql() -> str:
    # Markers are static, safe lowercase fragments — inline as LIKE patterns.
    return "ARRAY[" + ", ".join("'%%%s%%'" % m for m in KILL_MARKERS) + "]"


def _kill_where() -> str:
    arr = _markers_array_sql()
    return (
        "is_auto_generated = true AND ("
        f"lower(coalesce(generation_source,'')) LIKE ANY({arr}) "
        f"OR lower(coalesce(rule_metadata->>'source','')) LIKE ANY({arr}))"
    )


def upgrade() -> None:
    # 1. Re-source the genuine regulatory rules so the kill predicate spares them.
    for sub, new_source in KEEPERS.items():
        op.execute(
            "UPDATE rules SET rule_metadata = "
            "jsonb_set(coalesce(rule_metadata, '{}'::jsonb), '{source}', "
            f"to_jsonb('{new_source}'::text)) "
            f"WHERE is_auto_generated = true AND lower(rule_text) LIKE '%{sub}%'"
        )

    # 2. Snapshot the doomed rows (full row) so downgrade can restore verbatim.
    op.execute(
        f"CREATE TABLE IF NOT EXISTS {_AUDIT} AS "
        f"SELECT * FROM rules WHERE {_kill_where()}"
    )

    # 3. Deactivate in the source-of-truth table…
    op.execute(
        f"UPDATE rules SET is_active = false "
        f"WHERE id IN (SELECT id FROM {_AUDIT})"
    )
    # …and in the vector index analysis actually reads from (id is shared).
    op.execute(
        f"UPDATE rag_rules SET is_active = false "
        f"WHERE id IN (SELECT id FROM {_AUDIT})"
    )


def downgrade() -> None:
    # Reactivate in both stores, then revert the keeper re-sourcing.
    op.execute(
        f"UPDATE rules SET is_active = true "
        f"WHERE id IN (SELECT id FROM {_AUDIT})"
    )
    op.execute(
        f"UPDATE rag_rules SET is_active = true "
        f"WHERE id IN (SELECT id FROM {_AUDIT})"
    )
    for sub in KEEPERS:
        op.execute(
            "UPDATE rules SET rule_metadata = "
            "jsonb_set(coalesce(rule_metadata, '{}'::jsonb), '{source}', "
            f"to_jsonb('{_KEEPER_ORIGINAL_SOURCE}'::text)) "
            f"WHERE is_auto_generated = true AND lower(rule_text) LIKE '%{sub}%'"
        )
    op.execute(f"DROP TABLE IF EXISTS {_AUDIT}")
