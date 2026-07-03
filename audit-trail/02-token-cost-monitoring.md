# 02 · Token, Cost, Session & Re-run Monitoring

This is the **primary goal**: know who spends the LLM budget, on what, and how much. This file defines exactly what we capture,
where the numbers come from, how we attribute them, and how we turn tokens into money.

---

## 1. What we capture (the grain)

For **every LLM call** the system makes, record one **usage event** with:

- **Input / prompt tokens** and **output / completion tokens** (the two the user asked for: "ip token and op token").
- **Attribution chain:** `user_id → session_id → submission_id → run_id → llm call`, plus the **endpoint/feature**
  (`analysis` | `chat` | `quote` | `rewrite` | `rule_generation`) and the **LLM profile/model** (`main` gpt-5.4 |
  `critic` gpt-5.4-nano | `chat` | embeddings).
- **Cost:** `input_cost_usd`, `output_cost_usd`, `total_cost_usd` from the price table (§4).
- **Metadata:** provider, model/deployment, `latency_ms`, whether it was a retry/failover attempt, timestamp.

Aggregations the console needs then fall out of simple `GROUP BY`s over this ledger:

- per **user** (total tokens, total cost, over a date range),
- per **submission / document** (input tokens, output tokens, cost — exactly "for any doc user uploaded show its ip token and op
  token with usage"),
- per **run** (including re-runs),
- per **session**, per **model**, per **day/week**.

## 2. Where the numbers come from (real, not guessed)

`LLMService` **already** obtains the authoritative token counts and pushes them to LangSmith + the budget accounting. The
capture point is there.

- **Non-streaming calls** (`generate_response`, `generate_structured_response` in `backend/app/services/llm_service.py`): the
  SDK response carries `response.usage.prompt_tokens` and `response.usage.completion_tokens`. This is the exact spot that today
  feeds `_update_langsmith_usage(...)`. We add a `usage_recorder.record(...)` call alongside it.
- **Streaming calls** (`stream_response`, used by chat): the code already requests
  `stream_options={"include_usage": True}`, so the final chunk reports real usage. Capture it when the stream ends.
- **Fallback:** if a provider omits `usage` (rare), fall back to the existing `LLMService._estimate_tokens(...)` (tiktoken
  `cl100k_base`, else 4 chars/token) and mark the event `token_source = "estimated"` so the console can distinguish measured
  from estimated spend.
- **Embeddings** (`AzureCohereEmbedder`): Cohere embed responses report billed units; capture per batch and attribute to the
  triggering run (analysis) or to `system` (ingestion scripts).

> **Failover & retries are counted.** The multi-key failover loop and the schema-correction retry loop in
> `generate_structured_response` can make several billed calls for one logical grade. Each billed attempt writes its own usage
> event, so cost reflects reality (this is precisely the "money leak" the console must expose).

## 3. Attribution mechanism — the `usage_context` ContextVar

The problem: `LLMService` is deep below the HTTP layer and the LangGraph nodes; it doesn't know which user/submission triggered
a call. The solution mirrors the **existing `GraphContext` pattern** (`backend/app/services/agents/graph/context.py`), which
already carries the DB session into graph nodes via a `ContextVar`.

We add a parallel request-scoped context:

```python
# backend/app/services/observability/usage_context.py  (NEW)
from contextvars import ContextVar
from dataclasses import dataclass, replace

@dataclass(frozen=True)
class UsageContext:
    user_id: str | None = None
    session_id: str | None = None
    submission_id: str | None = None
    run_id: str | None = None          # analysis_runs.id
    feature: str = "unknown"           # analysis|chat|quote|rewrite|rule_generation|ingestion

_ctx: ContextVar[UsageContext] = ContextVar("usage_context", default=UsageContext())

def get_usage_context() -> UsageContext: return _ctx.get()
def set_usage_context(**kw): _ctx.set(replace(_ctx.get(), **kw))
def bind_usage_context(ctx: UsageContext): return _ctx.set(ctx)
```

**Who sets what:**

- **Auth middleware** sets `user_id` + `session_id` at the start of every authenticated request.
- **Route handlers** set `feature` (`chat`, `rewrite`, `rule_generation`, …) and `submission_id` where known.
- **`ComplianceEngine.analyze_submission`** opens an `analysis_runs` row and sets `run_id` + `submission_id` before invoking the
  graph — so every LLM call inside the graph (per-chunk grade, completeness sweep, critic, disclosure backstop) inherits the
  right attribution automatically. This is the same `finally`-reset discipline the engine already uses for `GraphContext`
  (`engine.py` sets the context before the run and resets it after).

**Who reads it:** `usage_recorder.record()` inside `LLMService` reads `get_usage_context()` and stamps the event.

Because `ContextVar`s propagate across `asyncio` tasks, the bounded-concurrency `asyncio.gather` over chunks (Semaphore = 2)
still carries each task's context correctly.

## 4. Cost model (tokens → money)

Cost = tokens × price. Prices are **per-model, configurable** (Decision D6) because they depend on your Azure/Groq contract.

### 4.1 Price configuration

Add to `Settings` (`backend/app/config.py`) a price map, in **USD per 1,000 tokens**, overridable by env:

```python
# Illustrative structure — FILL IN ACTUAL CONTRACT RATES. Do not treat these numbers as real.
llm_prices: dict[str, dict[str, float]] = {
    #  model / deployment name       input     output
    "gpt-5.4":            {"input": 0.0,  "output": 0.0},   # <-- set from Azure contract
    "gpt-5.4-nano":       {"input": 0.0,  "output": 0.0},   # critic
    "llama-3.3-70b-versatile": {"input": 0.0, "output": 0.0},  # groq (if used)
    "Cohere-embed-v3-multilingual": {"input": 0.0, "output": 0.0},  # embeddings (billed per input token)
}
llm_price_currency: str = "USD"
```

> **Honesty note:** exact gpt-5.4 / gpt-5.4-nano / Cohere prices are contract-specific and must be entered from your billing
> agreement. The plan ships the **formula and plumbing**; the numbers are yours to fill. Consider a small `model_prices` DB
> table editable from the console (D6 alt) so finance can update rates without a redeploy.

### 4.2 Cost calculation

```python
# backend/app/services/observability/cost.py  (NEW)
def compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> tuple[float, float, float]:
    rates = settings.llm_prices.get(model) or settings.llm_prices.get("_default", {"input": 0.0, "output": 0.0})
    input_cost  = (prompt_tokens     / 1000.0) * rates["input"]
    output_cost = (completion_tokens / 1000.0) * rates["output"]
    return input_cost, output_cost, input_cost + output_cost
```

An unknown model falls back to a `_default` rate and flags `price_source = "default"` so the console can warn that a model's
price is unconfigured (avoids silently under-reporting spend).

## 5. Re-run tracking (Decision: `analysis_runs` fact table)

**Why not reuse `compliance_checks`?** Because the engine's **fail-closed persistability gate** (`engine.evaluate_persistability`)
deliberately persists **no** `ComplianceCheck` for degraded/failed runs — yet those runs **already spent tokens**. If we counted
runs by `compliance_checks`, we would under-count money on exactly the runs most worth watching. So runs are tracked
independently.

`analysis_runs` (full DDL in [04](./04-database-schema.md)) captures, for **every** invocation of `analyze_submission`:

- `id`, `submission_id`, `triggered_by` (user_id), `session_id`
- `run_number` (1 = first, 2+ = re-run), `is_rerun` (bool)
- `trigger_source` (`sync` | `async` | `stream`)
- `status` (`running` → `completed` | `needs_review` | `failed`)
- `compliance_check_id` (nullable — null on fail-closed runs)
- `started_at`, `finished_at`, `duration_ms`
- **rollups:** `prompt_tokens`, `completion_tokens`, `total_tokens`, `total_cost_usd` (summed from that run's `llm_usage_events`)
- `degraded_reason` (mirrors `metadata["degraded"]` so the console shows *why* a run cost money but produced no grade)

**Where to open/close the row:** `ComplianceEngine.analyze_submission` — open on claim (right where it sets the submission to
`analyzing` under the row lock), close in the `finally`. `run_number` = `count(analysis_runs where submission_id=…) + 1`.

This directly satisfies *"there is re-run feature — the time a user re-runs should be tracked in the admin panel."* Each re-run
is a distinct `analysis_runs` row with its own actor, timestamp, duration, tokens, and cost.

## 6. Session-time tracking

- On login, insert a `user_sessions` row (`login_at`, `ip`, `user_agent`, `status='active'`).
- The auth middleware updates `last_seen_at` on each authenticated request (throttled to ≤ 1 write / 30s to avoid write
  amplification; Redis holds the hot `last_seen_at`, mirrored to Postgres periodically).
- The frontend sends a lightweight **heartbeat** (`POST /auth/heartbeat`) every ~60s while a tab is focused, so idle-but-open
  time is bounded and "active time" is meaningful.
- On logout or idle-expiry, set `logout_at` and `duration_seconds = logout_at − login_at`. For sessions that just expire, a
  small sweep (or lazy compute on read) closes them using `last_seen_at`.
- Console shows **per-user total active time**, **per-session duration**, and **currently-online** users.

## 7. Console rollups (example queries)

All console numbers derive from `llm_usage_events` + `analysis_runs` + `user_sessions` + `audit_events`.

```sql
-- Cost & tokens per user (last 30 days)  → "who is spending money"
SELECT u.username, u.role,
       SUM(e.prompt_tokens)      AS input_tokens,
       SUM(e.completion_tokens)  AS output_tokens,
       SUM(e.total_cost_usd)     AS cost_usd,
       COUNT(DISTINCT e.run_id)  AS runs
FROM llm_usage_events e JOIN users u ON u.id = e.user_id
WHERE e.created_at >= now() - interval '30 days'
GROUP BY u.username, u.role
ORDER BY cost_usd DESC;

-- Per-document (submission) input/output tokens + cost + #re-runs → the doc table in the console
SELECT s.id, s.title, u.username AS graded_by,
       SUM(e.prompt_tokens)     AS input_tokens,
       SUM(e.completion_tokens) AS output_tokens,
       SUM(e.total_cost_usd)    AS cost_usd,
       MAX(r.run_number)        AS total_runs
FROM submissions s
JOIN analysis_runs r     ON r.submission_id = s.id
LEFT JOIN llm_usage_events e ON e.run_id = r.id
LEFT JOIN users u        ON u.id = r.triggered_by
GROUP BY s.id, s.title, u.username
ORDER BY cost_usd DESC;

-- Re-run detail for one document
SELECT run_number, is_rerun, trigger_source, status, degraded_reason,
       started_at, duration_ms, prompt_tokens, completion_tokens, total_cost_usd
FROM analysis_runs WHERE submission_id = :sid ORDER BY run_number;

-- Session time per user (last 7 days)
SELECT u.username, COUNT(*) AS sessions,
       SUM(COALESCE(sess.duration_seconds,
                    EXTRACT(EPOCH FROM (COALESCE(sess.last_seen_at, now()) - sess.login_at)))) AS active_seconds
FROM user_sessions sess JOIN users u ON u.id = sess.user_id
WHERE sess.login_at >= now() - interval '7 days'
GROUP BY u.username;
```

## 8. Data-flow (summary)

```
HTTP request ──▶ auth middleware sets usage_context(user_id, session_id)
   route handler sets usage_context(feature, submission_id)
      ComplianceEngine opens analysis_runs row → set usage_context(run_id)
         graph nodes call LLMService (grade / sweep / critic / disclosure)
            LLMService reads real usage → usage_recorder.record():
               compute_cost(model, in, out) → INSERT llm_usage_events (attributed)
      engine finally: sum this run's events → update analysis_runs rollups + finished_at
   ── Super-Admin console reads GROUP BY rollups ──▶ tables & charts
```

## 9. Reliability / correctness notes

- **Never block a grade on a usage-write failure.** `usage_recorder.record()` is best-effort (try/except + log) — losing a
  metering row must not fail the compliance analysis. (Same philosophy as the existing non-fatal RAG indexing.)
- **Reconcile, don't double-count.** One usage event per *billed* LLM call. The `analysis_runs` rollup is a `SUM` over that
  run's events — computed once at run close, so re-reads are cheap and consistent.
- **Estimated vs measured** is tracked per event (`token_source`) so finance can trust the "measured" total and treat estimates
  as an upper-bound.
- **Ingestion / script usage** (e.g. `ingest_knowledge_base`, `ingest_precedent_cases`, which call the critic model to enrich)
  is attributed to a synthetic `system` actor so batch spend is visible but not blamed on a person.
