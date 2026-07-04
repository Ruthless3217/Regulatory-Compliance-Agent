# 00 · Overview, Goals, Scope & Current-State Analysis

## 1. Problem statement

The Regulatory Compliance Agent runs on an **internal UAT VM** reachable only over corporate Wi-Fi (the VM is not exposed to
the public internet). Today:

- There is **no authentication** — every visitor has full access to every feature (grading, rules editing, dashboards, chat).
- LLM tokens **are** metered inside `LLMService` for rate-limiting and a global daily budget, and per-execution counts land in
  `agent_executions.total_tokens_used` / `tool_invocations.tokens_used` — but **none of it is attributed to a person, a
  document, or a specific run**, and **cost (money) is never computed**.
- Compliance **rules can be changed by anyone**, with no record of *who* changed *what* beyond the existing version chain.

We cannot answer the business questions that matter: *Who is spending the LLM budget? On which documents? How many re-runs did
they trigger? How long are they in the app? Who changed this rule and why?*

## 2. Goals

**Primary goal — token & cost monitoring (the money lens).** For every LLM interaction, capture **input (prompt) tokens** and
**output (completion) tokens**, attribute them to **user → session → submission → run → LLM call**, compute **USD cost** from a
configurable per-model price table, and expose it all in a Super-Admin console.

**Supporting goals:**

1. **Authentication** — a login page; accounts provisioned by an admin with **username + password + registered IP**; login
   verifies all three and binds the session to the mapped IP.
2. **RBAC (three roles)** — `user`, `admin`, `super_admin` with the exact capabilities in §4.
3. **Re-run tracking** — every grading run (initial *and* re-run, *including* fail-closed runs that persist no result but still
   burn tokens) is recorded with its own token/cost.
4. **Session-time tracking** — measure how long each user is active.
5. **Rule-change audit** — who changed which rule, before → after.
6. **Tamper-evident audit trail** — an append-only log of security- and money-relevant events.
7. **Auth hardening** — defense in depth beyond the Wi-Fi boundary (see [08](./08-security-hardening.md)).

## 3. Non-goals (explicitly out of scope for this phase)

- Public-internet exposure, SSO/OAuth/SAML, or corporate AD/LDAP integration (the existing `firebase_uid` column stays unused).
- Changing the compliance grading logic, scoring, or RAG behaviour.
- Real-time billing integration with the LLM provider — we compute **estimated** cost from a configured price table, reconciled
  against the SDK's reported token `usage`.
- Multi-tenant isolation — this is a single internal team.

## 4. Personas & capability model

| Capability | user | admin | super_admin |
|------------|:----:|:-----:|:-----------:|
| Log in (IP + name + password) | ✅ | ✅ | ✅ |
| Upload / paste / URL a submission | ✅ | ✅ | ❌ |
| Run analysis (grade a document) | ✅ | ✅ | ❌ |
| Re-run analysis | ✅ | ✅ | ❌ |
| View Review / Report / Chat for a submission | ✅ | ✅ | ❌ |
| View analytics Dashboard | ✅ | ✅ | ❌ (has own console) |
| View Knowledge Base viz | ✅ | ✅ | ✅ (read) |
| View Rules library | ✅ (read-only) | ✅ | ✅ |
| **Create / edit / deactivate / delete rules** | ❌ | ✅ | ✅ |
| **Generate rules from a document** | ❌ | ✅ | ✅ |
| Submit violation feedback (adaptive weights) | ✅ | ✅ | ✅ |
| Document Comparison tool | ✅ | ✅ | ❌ |
| **Super-Admin console** (users, tokens, cost, sessions, runs) | ❌ | ❌ | ✅ |
| **Provision / edit users** | ❌ | ✅¹ | ✅ |
| **View rule-change audit (who changed what)** | ❌ | partial² | ✅ |

¹ *Decision point D3 (below): whether **admins** may also create users, or only super-admins. Default: super-admin creates all
accounts; admins may create only `user` accounts. See open decisions.*
² *Admins can see the rule version history that already exists; the full actor/IP audit view lives in the super-admin console.*

**Role summary in one line each:**
- **user** = document grader. Everything except rule mutation and the super-admin console.
- **admin** = compliance chief. A user *plus* rule authority (CRUD + generate) *plus* provisioning `user` accounts.
- **super_admin** = compliance head / platform owner. **Console only** — monitors users & admins, token/cost/session/re-run,
  and the rule-change audit; may also edit rules (every edit is itself audited). Does **not** grade documents.

## 5. Current-state analysis — what we can build on (leverage points)

This is the most important section for grounding the plan: much of the scaffolding already exists.

| Need | Already in the codebase | File |
|------|-------------------------|------|
| A users table with roles | `users(id, email, display_name, firebase_uid, role)` — `role` comment already says `# user, admin, super_admin` | `backend/app/models/user.py` |
| Client-IP extraction honoring a proxy | `extract_client_key(request, trust_forwarded_for=…)` + `settings.trust_forwarded_for` | `backend/app/api/rate_limit.py:44` |
| Redis for shared state | `get_redis()` / `init_redis()` (already used for rate-limit + budget + LangGraph checkpointer) | `backend/app/services/cache/redis_client.py` |
| Real token counts per LLM call | The SDK `usage` object (`prompt_tokens`, `completion_tokens`) is already read and pushed to LangSmith + budget | `backend/app/services/llm_service.py` (`_update_langsmith_usage`, `generate_structured_response`) |
| Per-execution token totals | `agent_executions.total_tokens_used`, `tool_invocations.tokens_used`, `latency_ms` | `backend/app/models/agent_execution.py`, `tool_invocation.py` |
| A request-scoped context pattern | `GraphContext` — a `ContextVar` carrying the DB session into graph nodes | `backend/app/services/agents/graph/context.py` |
| One run = one `ComplianceCheck` | Each analysis persists a check; multiple checks per submission ≈ re-runs (but degraded runs persist none) | `backend/app/services/agents/compliance/engine.py` |
| Rule versioning + actor column | `rules.version`, `superseded_by`, `created_by` (→ users); `PATCH /rules/{id}` already versions on content change | `backend/app/api/routes/rules.py`, `backend/app/models/rule.py` |
| A global token budget primitive | `GlobalTokenBudget.reserve/reconcile` (Redis day-keyed counter) | `backend/app/services/llm_budget.py` |
| Sub-path deploy for `/super_admin` | `NEXT_PUBLIC_BASE_PATH` support + `/api` rewrite | `frontend/next.config.ts` |
| Rate limiting + budget guards on paid endpoints | `Depends(llm_rate_limit)`, `Depends(llm_budget_guard)` | `backend/app/api/routes/{compliance,chat,rules}.py` |
| PII redaction for logs | `services/pii.py`, `services/redaction.py` | already applied to LLM logs |

**Implication:** we are mostly *wiring together and attributing* things the system already computes — not inventing token
metering from scratch. The single biggest new piece is a **request-scoped attribution `ContextVar`** (`usage_context`) that
carries `user_id / session_id / submission_id / run_id` down to `LLMService`, mirroring the existing `GraphContext` pattern.

## 6. Assumptions

1. The app is deployed behind **nginx** (`shared/nginx.conf`) which sets `X-Forwarded-For`; therefore `trust_forwarded_for`
   will be **enabled in this deployment** so the real employee IP (not the proxy IP) is read. This is a change from the current
   default (`False`) and is only safe because the proxy is trusted and the VM is not directly internet-facing.
2. Employee machines have **reasonably stable IPs** on the corporate network (static or long-lease DHCP). This assumption is
   load-bearing for IP binding — see decision **D1**.
3. Redis is available in this deployment (it is, per `docker-compose.yml`). Sessions degrade safely if it is not (see 01/08).
4. Token `usage` returned by the Azure OpenAI / Groq SDK is trustworthy for cost; where a provider omits it (rare streaming
   cases), we fall back to the existing `tiktoken` estimate already implemented in `LLMService._estimate_tokens`.
5. There is exactly one team; no need for org/tenant scoping.

## 7. Open decisions (resolve before Phase 1 — recommendations given)

| # | Decision | Recommendation | Alternatives |
|---|----------|----------------|--------------|
| **D1** | IP binding strictness given DHCP | **Bind to a single IP but let an admin update it in one click; on IP mismatch, deny + surface a clear "IP changed — ask an admin to re-map" message.** | (a) allow a small **CIDR/subnet** per user; (b) treat IP as a *logged signal only*, not a hard gate; (c) allow a list of IPs per user |
| **D2** | Session mechanism | **Redis server-side sessions** (opaque cookie) | Signed JWT (stateless, harder to revoke / to measure live session time) |
| **D3** | Who may provision users | **Super-admin provisions all; admin may create only `user` accounts** | Only super-admin provisions anyone |
| **D4** | Should `super_admin` grade documents? | **No** (console only, per the ask) | Allow, but exclude super-admin's own runs from team cost rollups |
| **D5** | Password reset flow | **Admin/super-admin resets by setting a new temporary password (user must change on next login)** | Self-service reset (needs email — out of scope on an isolated VM) |
| **D6** | Cost price source | **Config table `LLM_PRICE_*` per model, editable via env** (numbers filled from your Azure/Groq contract) | Hard-code; or a small `model_prices` DB table editable in the console |
| **D7** | Backfill historical usage | **No backfill** — monitoring starts at go-live (historical runs lack per-user attribution) | Best-effort backfill of `agent_executions` totals with `user_id = NULL` |

> These are captured again as a checklist in [09 · Roadmap](./09-implementation-roadmap.md).

## 8. Glossary

- **Run / analysis run** — one execution of the compliance graph for a submission (`POST /compliance/analyze/{id}*`). A
  **re-run** is any run after the first for the same submission.
- **Fail-closed run** — a run that ends in `needs_review`/`failed` and persists **no** `ComplianceCheck` (per the engine's
  persistability gate) but **still consumed tokens**. Must be counted for cost.
- **Usage event** — one row in `llm_usage_events`: a single LLM call's input/output tokens + cost, tagged with the full
  attribution chain.
- **Session** — the interval from login to logout/expiry for one user on one device/IP.
- **Principal / actor** — the authenticated user performing a request.
