"""Phase 4 (observability): the request-scoped ``usage_context`` ContextVar.

Mirrors the GraphContext pattern. Tests set/get plus the critical property that
ContextVars copy into ``asyncio`` tasks, so the bounded-concurrency chunk
fan-out attributes every child LLM call to the right run.
"""
import asyncio

from app.services.observability.usage_context import (
    UsageContext,
    bind_usage_context,
    get_usage_context,
    set_usage_context,
)


def test_default_context_is_unknown():
    bind_usage_context(UsageContext())  # baseline for this thread's context
    ctx = get_usage_context()
    assert ctx.user_id is None
    assert ctx.feature == "unknown"


def test_set_and_get_merges_fields():
    bind_usage_context(UsageContext())  # reset baseline
    set_usage_context(user_id="u-1", feature="chat")
    ctx = get_usage_context()
    assert ctx.user_id == "u-1"
    assert ctx.feature == "chat"
    # unset fields keep their defaults
    assert ctx.run_id is None
    # frozen dataclass: further set replaces, doesn't mutate in place
    set_usage_context(run_id="r-9")
    ctx2 = get_usage_context()
    assert ctx2.user_id == "u-1"       # preserved
    assert ctx2.run_id == "r-9"        # added
    assert ctx is not ctx2             # new frozen instance


def test_bind_replaces_whole_context():
    bind_usage_context(UsageContext(user_id="a", run_id="b", feature="analysis"))
    ctx = get_usage_context()
    assert (ctx.user_id, ctx.run_id, ctx.feature) == ("a", "b", "analysis")


def test_context_propagates_across_gather():
    """Each child task must see the context bound before gather()."""

    async def child():
        return get_usage_context().run_id

    async def main():
        bind_usage_context(UsageContext(user_id="U", run_id="RUN-1", feature="analysis"))
        return await asyncio.gather(child(), child(), child())

    results = asyncio.run(main())
    assert results == ["RUN-1", "RUN-1", "RUN-1"]


def test_child_mutation_does_not_leak_to_siblings_or_parent():
    """A child task's set_usage_context stays local to that task's copy."""

    async def child(feature):
        set_usage_context(feature=feature)
        # give the loop a chance to interleave the sibling tasks
        await asyncio.sleep(0)
        return get_usage_context().feature

    async def main():
        bind_usage_context(UsageContext(run_id="RUN-2", feature="analysis"))
        results = await asyncio.gather(child("chat"), child("rewrite"))
        parent_feature = get_usage_context().feature
        return results, parent_feature

    (results, parent_feature) = asyncio.run(main())
    assert set(results) == {"chat", "rewrite"}      # each kept its own
    assert parent_feature == "analysis"             # parent untouched
