# Audit Trail, Auth & Token-Monitoring — Design Plan

> **Status:** DRAFT / proposal for review · **Owner:** Platform · **Target app:** Regulatory Compliance Agent (internal UAT)
>
> This directory is a *design & implementation plan only*. No application code has been changed. It describes how to add
> (1) an authentication layer, (2) a three-role RBAC model, (3) a Super-Admin monitoring console at `…/super_admin`, and
> (4) end-to-end **token + cost + session + re-run monitoring** plus a tamper-evident **audit trail** — all grounded in the
> existing codebase (FastAPI + LangGraph backend, Next.js frontend, PostgreSQL + pgvector, Redis).

---

## Why this exists (the ask, restated)

The application is hosted on an **internal UAT VM**, reachable only from employees on the corporate Wi-Fi — the VM is **not
exposed to the public internet**. Today there is **no login and no per-user attribution**: anyone who can reach the VM can
use every feature, and while the backend already meters LLM tokens for rate-limiting/budget, it does **not attribute spend to
a person, a document, or a run**.

The goal is to know **who is spending money, on what, and how much** — where "money" ≈ LLM tokens (input + output) × model
price. Secondary goals: lock down *who can change compliance rules*, and keep a defensible record of *who did what*.

## The three roles (at a glance)

| Role | Who | Can grade docs? | Can change rules? | Sees Super-Admin console? | Primary surface |
|------|-----|:---:|:---:|:---:|-----------------|
| **user** | Content / marketing staff | ✅ | ❌ (read-only) | ❌ | Grading workspace (all dashboards **except** rule editing) |
| **admin** | Compliance chief / manager | ✅ | ✅ | ❌ | Full grading workspace **+** Rules CRUD + rule generation |
| **super_admin** | Compliance head / platform owner | ❌ | ✅ (with full change-audit) | ✅ | **Console only** — user/admin monitoring, token & cost, sessions, re-runs, rule-change audit |

## What gets built

- **Login** at `…/login` shown to everyone on first visit. The Super-Admin surface is **not linked** anywhere — it lives at
  the deliberately separate, unlisted URL `…/super_admin` and is guarded server-side.
- **Admin-provisioned users**: an admin/super-admin creates each account with **name (username) + password + registered IP**.
  On login all three are checked; the session is bound to the mapped IP.
- **Per-request attribution**: every LLM call (analysis, chat, rewrite, rule-generation) is tagged with the acting user,
  session, submission, and run, and written to a **usage ledger** with **input tokens, output tokens, and computed USD cost**.
- **Re-run tracking**: every grading run (including re-runs and fail-closed runs that persist *no* result but still burn
  tokens) is recorded as a first-class `analysis_run` with its own token/cost totals.
- **Session-time tracking**: login → heartbeat → logout, so the console can show active time per user.
- **Rule-change audit**: every rule create/update/deactivate/delete records the actor, IP, and a before/after snapshot.

## How to read this plan

Read in order; each file is self-contained but builds on the previous one.

| # | File | What it covers |
|---|------|----------------|
| — | [`README.md`](./README.md) | This index + executive summary |
| 00 | [`00-overview-goals-scope.md`](./00-overview-goals-scope.md) | Goals, non-goals, personas, **current-state leverage points**, assumptions & open decisions |
| 01 | [`01-auth-rbac-design.md`](./01-auth-rbac-design.md) | Auth model (IP + username + password), sessions, RBAC permission matrix, user provisioning, bootstrap |
| 02 | [`02-token-cost-monitoring.md`](./02-token-cost-monitoring.md) | Token capture, attribution `ContextVar`, cost model, re-run & session tracking, console rollups |
| 03 | [`03-audit-trail-events.md`](./03-audit-trail-events.md) | Audit event taxonomy, rule-change audit, immutability, retention |
| 04 | [`04-database-schema.md`](./04-database-schema.md) | New tables + `users` ALTER, full DDL, Alembic migration plan, indexes |
| 05 | [`05-backend-implementation.md`](./05-backend-implementation.md) | Auth service, FastAPI deps, middleware, usage recorder, cost calc, route wiring, config |
| 06 | [`06-frontend-implementation.md`](./06-frontend-implementation.md) | Login page, route gating, role-aware nav, Super-Admin console pages, API client |
| 07 | [`07-workflow-diagrams.md`](./07-workflow-diagrams.md) | Mermaid diagrams: login, provisioning, token capture, re-run, rule audit, RBAC, session lifecycle |
| 08 | [`08-security-hardening.md`](./08-security-hardening.md) | Threat model + hardening checklist (what exists vs. what to add) |
| 09 | [`09-implementation-roadmap.md`](./09-implementation-roadmap.md) | Phased rollout, migration order, testing, acceptance criteria |

## Key design decisions (recommended defaults — see 00 for the full list)

1. **Server-side sessions in Redis** (opaque `httpOnly` cookie), not JWT — Redis is already a dependency, and revocation +
   live session-time tracking are core requirements. *(Alternative: signed JWT — documented in 01.)*
2. **New `analysis_runs` fact table** rather than reusing `compliance_checks`, because fail-closed/degraded runs persist **no**
   `ComplianceCheck` but **still consume tokens** — and those must be counted to monitor money.
3. **New `llm_usage_events` ledger** for per-user/per-run token + cost attribution, populated from the **real `usage` object**
   the LLM SDK already returns inside `LLMService`. The existing `agent_executions.total_tokens_used` /
   `tool_invocations.tokens_used` columns are kept but are per-execution, not per-user/cost.
4. **IP binding reuses the existing `extract_client_key()`** (`backend/app/api/rate_limit.py`) and the existing
   `settings.trust_forwarded_for` flag — the app sits behind nginx (`shared/nginx.conf`), so the real client IP comes from
   `X-Forwarded-For`.
5. **Append-only audit** — `audit_events` is written but never updated/deleted by the app; enforced by a DB role + trigger.

## Guardrails honored from the existing architecture

This plan preserves the system's stated principles (see `docs/ARCHITECTURE.md`): **fail closed** (auth failures deny, never
default-allow), **audit-traceable** (versioned rules, immutable event log), and **defense in depth** (network isolation is a
layer, *not* the only layer).
