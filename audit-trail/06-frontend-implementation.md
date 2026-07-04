# 06 · Frontend Implementation

Next.js 15 (App Router) + React 19 + TypeScript + Tailwind. The plan reuses the existing design system (token-based Tailwind
colours, `components/ui/*` shadcn primitives, `lib/api.ts` typed fetch, the `(workspace)` route group) and adds a login page, a
separate `/super_admin` console, and role-aware gating.

---

## 1. Route structure

```
app/
├── login/page.tsx                     # NEW — public login (no sidebar/topbar)
├── account/change-password/page.tsx   # NEW — forced first-login password change
├── (workspace)/…                      # EXISTING — user & admin surface (grading, dashboards, rules)
│   └── layout.tsx                     # gains auth guard + role-aware sidebar
└── (super-admin)/                     # NEW route group — isolated console shell
    └── super_admin/
        ├── layout.tsx                 # console chrome (NOT the workspace sidebar)
        ├── page.tsx                   # console home: cost/tokens overview
        ├── users/page.tsx             # user management + provisioning
        ├── usage/page.tsx             # per-user & per-document token+cost tables
        ├── runs/page.tsx              # analysis runs incl. re-runs
        ├── sessions/page.tsx          # session-time / who's online
        ├── audit/page.tsx             # audit event feed
        └── rules/page.tsx             # rule monitoring + who-changed-what + (optional) edit
```

- `/super_admin` sits in its **own route group** so it does **not** inherit the workspace `Sidebar`/`TopBar`. It is never linked
  from workspace nav.
- The URL respects `NEXT_PUBLIC_BASE_PATH` (already supported in `frontend/next.config.ts`), so a sub-path deploy yields
  `…/compliance/super_admin` exactly as requested.

---

## 2. Route gating — `middleware.ts` (NEW, repo root of `frontend/`)

Next.js middleware runs at the edge before pages render. It does the coarse redirect; the backend remains authoritative.

```ts
// frontend/middleware.ts
import { NextResponse } from "next/server";
export function middleware(req) {
  const { pathname } = req.nextUrl;
  const hasSession = req.cookies.has("rca_session");
  const isPublic = pathname === "/login" || pathname.startsWith("/api/auth/login");
  if (!hasSession && !isPublic) return NextResponse.redirect(new URL("/login", req.url));
  if (hasSession && pathname === "/login") return NextResponse.redirect(new URL("/", req.url));
  return NextResponse.next();
}
export const config = { matcher: ["/((?!_next|favicon|assets).*)"] };
```

Role-precise gating (who may see `/super_admin`, who may see workspace) happens in the **server layouts** by calling
`/auth/me` — because the opaque cookie carries no role, only the backend can resolve it:

```tsx
// app/(super-admin)/super_admin/layout.tsx  (server component)
const me = await getMe();                                   // GET /auth/me
if (!me || me.role !== "super_admin") notFound();           // 404 — don't advertise the route
```

```tsx
// app/(workspace)/layout.tsx  — add near the top
const me = await getMe();
if (!me) redirect("/login");
if (me.must_change_password) redirect("/account/change-password");
if (me.role === "super_admin") redirect("/super_admin");     // super-admin has no workspace
```

---

## 3. Auth client & context

### 3.1 API client additions (`frontend/lib/api.ts`)

Extend the existing typed-fetch module (same `jsonFetch` helper, same dual base-URL logic). All auth calls use
`credentials: "include"` so the `httpOnly` cookie rides along.

```ts
export const login   = (b: {username:string; password:string}) => jsonFetch("/auth/login", {method:"POST", body:JSON.stringify(b), credentials:"include"});
export const logout  = () => jsonFetch("/auth/logout", {method:"POST", credentials:"include"});
export const getMe   = () => jsonFetch<Me>("/auth/me", {credentials:"include"});
export const heartbeat = () => jsonFetch("/auth/heartbeat", {method:"POST", credentials:"include"});
export const changePassword = (b) => jsonFetch("/auth/change-password", {method:"POST", body:JSON.stringify(b), credentials:"include"});
// super-admin console
export const listUsers = () => jsonFetch<UserRow[]>("/super_admin/users", {credentials:"include"});
export const createUser = (b) => jsonFetch("/super_admin/users", {method:"POST", body:JSON.stringify(b), credentials:"include"});
export const usageSummary = (q) => jsonFetch<UsageSummary>(`/super_admin/usage/summary?${q}`, {credentials:"include"});
export const usageByDocument = (q) => jsonFetch<DocUsageRow[]>(`/super_admin/usage/by-document?${q}`, {credentials:"include"});
export const listRuns = (q) => jsonFetch<RunRow[]>(`/super_admin/runs?${q}`, {credentials:"include"});
export const listSessions = (q) => jsonFetch<SessionRow[]>(`/super_admin/sessions?${q}`, {credentials:"include"});
export const auditFeed = (q) => jsonFetch<AuditRow[]>(`/super_admin/audit?${q}`, {credentials:"include"});
export const ruleAudit = (q) => jsonFetch<RuleAuditRow[]>(`/super_admin/rules/audit?${q}`, {credentials:"include"});
```

New types go in `frontend/lib/types.ts` (`Me`, `UserRow`, `UsageSummary`, `DocUsageRow`, `RunRow`, `SessionRow`, `AuditRow`,
`RuleAuditRow`).

### 3.2 `useAuth` context (client)

A small `AuthProvider` (mirroring the existing `CommandPaletteProvider` pattern) that holds `me`, exposes `role`, and runs the
**heartbeat** interval (`useEffect` + `setInterval(heartbeat, 60_000)` while `document.visibilityState === "visible"`). Used by
components to conditionally render (UX only — never the security boundary).

---

## 4. Role-aware workspace nav (`components/workspace/Sidebar.tsx`)

The sidebar currently lists Workspace / Library / Insights / Settings groups. Gate the **rule-mutation** entries by role:

- **user:** Rules entry is visible but **read-only** — hide/disable "Generate rules", and inside `/rules` the table hides
  create/edit/deactivate/delete controls (`RulesTable.tsx` already centralises those; wrap them in `role === "admin"`).
- **admin:** full Library group (Rules + Generate rules).
- The `Sidebar` reads `role` from `useAuth`. Add a footer item: current user + **Logout** button (calls `logout()` →
  redirect `/login`). The existing `ApiHealthDot` stays.

`RulesTable.tsx` and `RuleGeneratorWizard.tsx` gain a `canEdit = role !== "user"` guard: for a `user`, mutation buttons are not
rendered and the API would 403 anyway (defense in depth).

> **Important:** hiding buttons is convenience, not enforcement. A `user` who crafts a `PATCH /rules` request still gets **403**
> from `require("rules:write")`. The UI gate just avoids showing dead controls.

---

## 5. Login page (`app/login/page.tsx`)

- Standalone page — no workspace chrome. Bajaj-blue brand panel + a card with **Username** + **Password** fields (IP is **not**
  a field; the server reads it).
- On submit → `login()` → on success route by role (`super_admin` → `/super_admin`, else `/`); if `must_change_password` →
  `/account/change-password`.
- Error states map the backend responses from [01 §5](./01-auth-rbac-design.md): invalid credentials (401), IP not recognised
  (403 → actionable message), locked out (429 → countdown), service unavailable (503).
- Reuse `components/ui/{input,button,card}.tsx`.

ASCII wireframe:

```
┌───────────────────────────── Regulatory Compliance Agent ─────────────────────────────┐
│                                                                                        │
│     ┌──────────────────────────────┐                                                   │
│     │  Sign in                      │      "Internal access only. Contact your         │
│     │  Username [________________]  │       compliance admin for an account."          │
│     │  Password [________________]  │                                                   │
│     │            [   Sign in    ]   │                                                   │
│     │  ⚠ This device isn't recognised… (on ip_mismatch)                                 │
│     └──────────────────────────────┘                                                   │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Super-Admin console pages

Console chrome = a slim left rail (Overview · Users · Usage & Cost · Runs · Sessions · Audit · Rules) — **not** the grading
sidebar. Charts reuse the existing Recharts setup (`components/dashboard/*` patterns) and the `dataviz` conventions.

### 6.1 Overview (`/super_admin`)
KPI tiles: **Total cost (period)**, **Total tokens (in / out)**, **Active users**, **Runs (incl. re-runs)**, **Avg cost/run**.
A cost-over-time area chart + top-5 spenders bar + top-5 most-expensive documents.

### 6.2 Users (`/super_admin/users`)
Table: username · role · registered IP · status · last login · #runs · total cost. Actions: **Create user** (username, temp
password, IP, role), **Edit** (IP/role/active), **Reset password**, **Force logout**. This is the provisioning surface from
[01 §3](./01-auth-rbac-design.md).

### 6.3 Usage & Cost (`/super_admin/usage`) — the core screen
Two tables driven by [02 §7](./02-token-cost-monitoring.md):

- **By user:** username · input tokens · output tokens · total tokens · cost · #runs · #sessions. Date-range picker.
- **By document:** submission title · graded by · **input tokens · output tokens** · cost · **# runs (incl. re-runs)** · last run.
  Click a row → drill into that document's runs.

```
By document (last 30 days)
Title                     Graded by   In-tok   Out-tok   Cost($)  Runs  Last run
Smart Secure GEO copy     r.sharma    182,400  24,110    2.41      3     2026-07-02
Term Plan landing v4      a.khan       98,700  11,050    1.02      1     2026-07-01
…                                                     [ Export CSV ]
```

### 6.4 Runs (`/super_admin/runs`)
Every `analysis_run`: document · user · run # · **is re-run** · trigger (sync/async/stream) · status · **degraded reason** ·
duration · in/out tokens · cost. Filter by user/date/status. This is where re-run spend and fail-closed-but-costed runs surface.

### 6.5 Sessions (`/super_admin/sessions`)
Who's online now (green dot) + historical sessions: user · IP · login · last seen · duration · status. Per-user total active
time for the period.

### 6.6 Audit (`/super_admin/audit`)
Reverse-chronological `audit_events` feed with filters (actor, event type, date, target). Login history + anomalies.

### 6.7 Rules (`/super_admin/rules`)
- **Rule-change timeline:** who changed which rule, before → after, when — from `rule_*` audit events + the version chain.
- **Optional inline edit** (super-admin may change rules; every edit writes a `rule_updated` event with
  `actor_role=super_admin`). Reuses the rules PATCH API.

---

## 7. Session-time on the client

- `AuthProvider` pings `POST /auth/heartbeat` every 60s while the tab is visible (Page Visibility API) so idle-with-tab-open
  time is bounded and "active time" is meaningful.
- On explicit logout, call `logout()` (closes the server session + `user_sessions` row) then redirect to `/login`.

---

## 8. What changes vs. what's new (summary)

| Area | Change |
|------|--------|
| `middleware.ts` | **new** — cookie-presence redirect |
| `app/login`, `app/account/change-password` | **new** pages |
| `app/(super-admin)/super_admin/*` | **new** route group + 7 pages |
| `app/(workspace)/layout.tsx` | add `getMe()` guard + role redirects |
| `components/workspace/Sidebar.tsx` | role-aware nav + user/logout footer |
| `components/rules/RulesTable.tsx`, `RuleGeneratorWizard.tsx` | `canEdit` guard for `user` role |
| `lib/api.ts`, `lib/types.ts` | auth + console endpoints/types |
| `AuthProvider` (new context) | holds `me`, runs heartbeat |
| existing fetches | add `credentials:"include"` so the session cookie is sent |

No change to the grading, review, report, chat, compare, or knowledge-base feature logic — only the **wrapping** (auth gate +
role visibility). The console is entirely additive.
