"""Phase 5: rule mutations emit audit events with actor + before/after.

Calls the route handlers directly with stubs (no TestClient/DB/RAG). Verifies the
event taxonomy and before/after snapshots wired in rules.py.
"""
import os
import sys
import asyncio
import types
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import app.api.routes.rules as rules_mod


def _rule(**kw):
    base = dict(id="rule-1", category="brand", rule_text="No superlatives.",
                severity="high", is_active=True, version=1, points_deduction=-5.0)
    base.update(kw)
    return types.SimpleNamespace(**base)


class _Req:
    def __init__(self):
        self.headers = {}
        self.client = types.SimpleNamespace(host="10.0.0.1")
        self.state = types.SimpleNamespace(session_id="sid-1")


class _DelDB:
    def __init__(self, rule):
        self._rule = rule
        self.deleted = False

    def query(self, *a):
        return self

    def filter(self, *a):
        return self

    def first(self):
        return self._rule

    def delete(self, obj):
        self.deleted = True

    def commit(self):
        pass


def test_create_rule_emits_rule_created():
    user = types.SimpleNamespace(id="u1", role="admin")
    created = _rule(id="new-rule")
    with patch.object(rules_mod.rule_generator_service, "create_rule", return_value=created), \
         patch.object(rules_mod, "_safe_rag_upsert", new=AsyncMock()), \
         patch.object(rules_mod.audit, "record", new=AsyncMock()) as rec:
        body = types.SimpleNamespace(category="brand", rule_text="No superlatives.",
                                     severity="high", keywords=[], points_deduction=-5.0)
        asyncio.run(rules_mod.create_rule(body, _Req(), db=object(), user=user))
        rec.assert_awaited_once()
        assert rec.await_args.args[0] == "rule_created"
        assert rec.await_args.kwargs["after"]["id"] == "new-rule"
        assert rec.await_args.kwargs["actor"] is user


def test_delete_rule_emits_rule_deleted_with_before():
    user = types.SimpleNamespace(id="u1", role="super_admin")
    victim = _rule(id="doomed")
    with patch.object(rules_mod, "_safe_rag_delete", new=AsyncMock()), \
         patch.object(rules_mod.audit, "record", new=AsyncMock()) as rec:
        asyncio.run(rules_mod.delete_rule("doomed", _Req(), db=_DelDB(victim), user=user))
        rec.assert_awaited_once()
        assert rec.await_args.args[0] == "rule_deleted"
        assert rec.await_args.kwargs["before"]["id"] == "doomed"
