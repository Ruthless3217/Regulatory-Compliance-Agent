"""`audit_events` carries no foreign keys.

The table has an append-only trigger (`trg_audit_events_no_update`, BEFORE
DELETE OR UPDATE) that raises "audit_events is append-only". A foreign key with
ON DELETE SET NULL issues exactly that forbidden UPDATE, so any FK pointing out
of this table turns deleting the referenced row into a 500:

    DELETE FROM submissions WHERE id = ...
      -> UPDATE audit_events SET scope_submission_id = NULL
        -> RAISE audit_events is append-only

`scope_submission_id` (0041) shipped with such an FK and broke
`DELETE /submissions/{id}` for every document that had ever been edited.
`actor_user_id` carried the same defect from the original auth work; it was
latent only because users are deactivated rather than deleted. 0042 drops both.

Storing the id without an FK is the right shape regardless: a trail has to
outlive the thing it describes, or it cannot answer "what happened to the
document we deleted?".
"""
from app.models.audit_event import AuditEvent


def _fks(column_name):
    return list(AuditEvent.__table__.c[column_name].foreign_keys)


def test_scope_submission_id_has_no_foreign_key():
    assert _fks("scope_submission_id") == [], (
        "an FK here makes DELETE /submissions/{id} raise on the append-only trigger"
    )


def test_actor_user_id_has_no_foreign_key():
    assert _fks("actor_user_id") == [], (
        "an FK here would make deleting a user raise on the append-only trigger"
    )


def test_the_columns_still_exist_and_are_nullable():
    """Dropping the constraint must not drop the value — the trail still has to
    say which document and which actor, even after both are gone."""
    for name in ("scope_submission_id", "actor_user_id"):
        col = AuditEvent.__table__.c[name]
        assert col.nullable is True
        assert col.type.python_type is not None


def test_audit_events_has_no_foreign_keys_at_all():
    """A blanket guard: any future FK added here reintroduces the same 500."""
    assert AuditEvent.__table__.foreign_keys == set()
