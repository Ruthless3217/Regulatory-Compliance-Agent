"""Phase 1 (audit-trail): Alembic migration-chain integrity.

Reads the migration scripts off disk (no DB connection) and asserts the auth +
monitoring + audit revisions form a single linear chain on top of the previous
head 0013 — so `alembic upgrade head` stays unambiguous.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(os.path.join(_BACKEND, "alembic.ini")))


def test_single_head_is_0016():
    assert _script().get_heads() == ["0016"]


def test_new_revisions_present_and_linear():
    script = _script()
    down = {rev.revision: rev.down_revision for rev in script.walk_revisions()}
    assert down["0014"] == "0013"
    assert down["0015"] == "0014"
    assert down["0016"] == "0015"


def test_every_new_migration_has_downgrade():
    script = _script()
    for rev_id in ("0014", "0015", "0016"):
        rev = script.get_revision(rev_id)
        module = rev.module
        assert hasattr(module, "upgrade")
        assert hasattr(module, "downgrade")
