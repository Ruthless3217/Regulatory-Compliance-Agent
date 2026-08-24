"""Drop the foreign keys on audit_events.

`audit_events` is append-only, enforced in the database by
`trg_audit_events_no_update` (BEFORE DELETE OR UPDATE), which raises
"audit_events is append-only".

Both foreign keys on the table used ON DELETE SET NULL, and SET NULL is
implemented as an UPDATE of the referencing row — exactly what that trigger
forbids. So the FKs did not degrade deletion gracefully; they made it raise:

    DELETE FROM submissions WHERE id = ...
      -> UPDATE audit_events SET scope_submission_id = NULL
        -> RAISE audit_events is append-only

`fk_audit_events_scope_submission_id` shipped in 0041 and broke
`DELETE /submissions/{id}` for any document that had ever been edited,
commented on, or assigned. `audit_events_actor_user_id_fkey` predates it and
carried the same defect, latent only because the app deactivates users instead
of deleting them.

The columns and their indexes stay. Storing the ids without referential
integrity is the correct shape for an audit table: the trail has to outlive the
thing it describes, or it cannot answer "what happened to the document we
deleted, and who did it?".

Revision ID: 0042
Revises: 0041
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0042"
down_revision: Union[str, None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (constraint name, referenced table, column) — dropped here, restored on downgrade.
_FKS = (
    ("fk_audit_events_scope_submission_id", "submissions", "scope_submission_id"),
    ("audit_events_actor_user_id_fkey", "users", "actor_user_id"),
)


def upgrade() -> None:
    # IF EXISTS: 0041 is the only place the scope FK is created, but the actor
    # FK's name is Postgres-generated and may differ on a database built by a
    # different route. A missing constraint is the state we want anyway.
    for name, _table, _col in _FKS:
        op.execute(sa.text(f'ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS "{name}"'))


def downgrade() -> None:
    # Restores the constraints as they were. Note this reinstates the bug: with
    # these in place, deleting a referenced submission or user raises on the
    # append-only trigger.
    for name, table, col in _FKS:
        op.create_foreign_key(
            name, "audit_events", table, [col], ["id"], ondelete="SET NULL",
        )
