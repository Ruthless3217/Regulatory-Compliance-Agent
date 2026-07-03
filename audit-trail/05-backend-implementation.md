# 05 · Backend Implementation

FastAPI + SQLAlchemy (async) + Redis. All sketches below reference **real files** in the repo and follow existing patterns
(the `Depends(llm_rate_limit)` dependency style, the `GraphContext` `ContextVar`, the `settings` singleton, the script layout).

---

## 1. New modules (directory layout)

```
backend/app/
├── auth/                              # NEW — authentication & RBAC
│   ├── __init__.py
│   ├── passwords.py                   # argon2/bcrypt hash + verify
│   ├── sessions.py                    # Redis session store (create/load/refresh/revoke)
│   ├── dependencies.py                # get_current_user, require(permission), ip_guard
│   ├── permissions.py                 # PERMISSIONS + ROLE_PERMISSIONS map
│   └── middleware.py                  # session load + last_seen refresh + usage_context bind
├── services/observability/           # NEW — token/cost/audit
│   ├── __init__.py
│   ├── usage_context.py              # ContextVar (see 02 §3)
│   ├── usage_recorder.py             # write llm_usage_events (best-effort)
│   ├── cost.py                       # compute_cost(model, in, out)
│   └── audit.py                      # audit.record(...)
├── services/run_tracker.py           # NEW — open/close analysis_runs rows
├── api/routes/
│   ├── auth.py                       # NEW — /auth/login, /logout, /me, /heartbeat, /change-password
│   └── admin_console.py              # NEW — /super_admin/* (users, usage, sessions, runs, audit, rules-audit)
└── models/                           # NEW models (see 04 §9)
    ├── user_session.py  analysis_run.py  llm_usage_event.py  audit_event.py
```

---

## 2. Authentication

### 2.1 Passwords (`auth/passwords.py`)

```python
from argon2 import PasswordHasher            # argon2-cffi
from argon2.exceptions import VerifyMismatchError
_ph = PasswordHasher()

def hash_password(raw: str) -> str: return _ph.hash(raw)

def verify_password(raw: str, hashed: str) -> bool:
    try: return _ph.verify(hashed, raw)
    except VerifyMismatchError: return False
```

### 2.2 Sessions (`auth/sessions.py`) — Redis-backed

```python
import secrets, json
from app.services.cache.redis_client import get_redis   # EXISTING

SESSION_TTL = 8 * 3600        # absolute
IDLE_TTL    = 60 * 60         # sliding

async def create_session(user, ip, user_agent) -> str:
    sid = secrets.token_urlsafe(32)
    r = await get_redis()
    if r is None: raise RuntimeError("session store unavailable")   # fail closed (see 01 §1.3)
    payload = {"user_id": str(user.id), "role": user.role, "ip": ip,
               "user_agent": user_agent, "must_change_password": user.must_change_password}
    await r.set(f"session:{sid}", json.dumps(payload), ex=SESSION_TTL)
    return sid

async def load_session(sid: str) -> dict | None:
    r = await get_redis()
    if r is None: return None
    raw = await r.get(f"session:{sid}")
    if not raw: return None
    await r.expire(f"session:{sid}", SESSION_TTL)   # sliding refresh
    return json.loads(raw)

async def revoke_session(sid: str): 
    r = await get_redis(); 
    if r: await r.delete(f"session:{sid}")

async def revoke_all_for_user(user_id: str):
    r = await get_redis()
    if not r: return
    async for k in r.scan_iter(match="session:*"):
        raw = await r.get(k)
        if raw and json.loads(raw).get("user_id") == user_id: await r.delete(k)
```

### 2.3 Permissions (`auth/permissions.py`)

```python
ROLE_PERMISSIONS = {
    "user":  {"submission:create","submission:read","submission:delete","analysis:run",
              "chat:use","comparison:use","dashboard:view","knowledgebase:view",
              "rules:read","feedback:submit"},
    "admin": {  # user + rule authority + user provisioning (user accounts only)
        "submission:create","submission:read","submission:delete","analysis:run","chat:use",
        "comparison:use","dashboard:view","knowledgebase:view","rules:read","feedback:submit",
        "rules:write","rules:generate","users:manage:user"},
    "super_admin": {  # console + governance; NO grading
        "knowledgebase:view","rules:read","rules:write","rules:generate","feedback:submit",
        "console:view","users:manage","audit:view","usage:view"},
}
def role_has(role: str, perm: str) -> bool:
    return perm in ROLE_PERMISSIONS.get(role, set())
```

### 2.4 Dependencies (`auth/dependencies.py`)

```python
from fastapi import Depends, HTTPException, Request
from app.api.rate_limit import extract_client_key            # EXISTING — reuse for IP
from app.config import settings
from .sessions import load_session
from .permissions import role_has

async def get_current_user(request: Request, db = Depends(get_db)):
    sid = request.cookies.get("rca_session")
    sess = await load_session(sid) if sid else None
    if not sess:
        raise HTTPException(401, "Not authenticated")
    # Re-bind IP check (cookie theft defense)
    if settings.auth_ip_binding_mode != "log_only":
        client_ip = extract_client_key(request, trust_forwarded_for=settings.trust_forwarded_for)
        if not ip_allowed(sess, client_ip):
            raise HTTPException(401, "Session ended — please log in again.")
    user = await db.get(User, sess["user_id"])
    if not user or not user.is_active:
        raise HTTPException(401, "Not authenticated")
    request.state.session_id = sid
    return user

def require(permission: str):
    async def _dep(request: Request, user = Depends(get_current_user)):
        if not role_has(user.role, permission):
            await audit.record(event_type="authz_denied", actor=user, request=request,
                               metadata={"permission": permission, "path": request.url.path})
            raise HTTPException(403, "You don't have access to this.")
        return user
    return _dep
```

`ip_allowed(sess_or_user, ip)` implements the four D1 modes (`strict` exact match, `cidr` via `ipaddress.ip_network`, `list`,
`log_only` always true but logs anomalies).

### 2.5 Middleware (`auth/middleware.py`)

A single ASGI/HTTP middleware that, for authenticated requests: loads the session, binds `usage_context(user_id, session_id)`,
and throttle-refreshes `last_seen_at` (Redis hot value + periodic Postgres mirror). Registered in `main.py` next to the existing
CORS middleware.

### 2.6 Auth routes (`api/routes/auth.py`)

```python
router = APIRouter(prefix="/auth", tags=["Auth"])

@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, db = Depends(get_db)):
    user = await get_user_by_username(db, body.username)
    ip = extract_client_key(request, trust_forwarded_for=settings.trust_forwarded_for)
    if not user or not user.is_active or not verify_password(body.password, user.password_hash or ""):
        await audit.record("login_failed", actor=user, request=request, metadata={"reason": "bad_credentials"})
        raise HTTPException(401, "Invalid username or password.")
    if settings.auth_ip_binding_mode == "strict" and not ip_allowed(user, ip):
        await audit.record("login_failed", actor=user, request=request, metadata={"reason": "ip_mismatch", "ip": ip})
        raise HTTPException(403, "This device isn't recognised for your account. Ask an admin to update your IP.")
    sid = await create_session(user, ip, request.headers.get("user-agent",""))
    await open_user_session_row(db, sid, user, ip, request)           # user_sessions insert
    user.last_login_at = func.now(); await db.commit()
    await audit.record("login", actor=user, request=request)
    response.set_cookie("rca_session", sid, httponly=True, secure=True, samesite="strict", max_age=SESSION_TTL)
    return {"role": user.role, "must_change_password": user.must_change_password}

@router.post("/logout")   # revoke session + close user_sessions + audit
@router.get("/me")        # returns {id, username, role, must_change_password} for the frontend
@router.post("/heartbeat")# updates last_seen (session-time)
@router.post("/change-password")  # self-service; clears must_change_password; revokes other sessions
```

Add a **login rate-limiter / lockout** (reuse the `FixedWindowLimiter` pattern from `rate_limit.py`, keyed on
`username`+`ip`) — see [08](./08-security-hardening.md).

---

## 3. Token/cost capture

### 3.1 Usage recorder (`services/observability/usage_recorder.py`)

```python
from .usage_context import get_usage_context
from .cost import compute_cost

async def record(*, model, provider, profile, prompt_tokens, completion_tokens,
                 latency_ms=None, is_retry=False, token_source="measured"):
    try:
        ctx = get_usage_context()
        in_cost, out_cost, total = compute_cost(model, prompt_tokens, completion_tokens)
        await insert_usage_event(                     # own short-lived DB session; best-effort
            user_id=ctx.user_id, session_id=ctx.session_id, submission_id=ctx.submission_id,
            run_id=ctx.run_id, feature=ctx.feature, profile=profile, provider=provider, model=model,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            total_tokens=prompt_tokens+completion_tokens,
            input_cost_usd=in_cost, output_cost_usd=out_cost, total_cost_usd=total,
            token_source=token_source, latency_ms=latency_ms, is_retry=is_retry)
    except Exception as e:
        logger.warning("usage_recorder: dropped a usage event: %s", e)   # NEVER raise
```

### 3.2 Hook into `LLMService` (`backend/app/services/llm_service.py`)

At the exact points that already read `response.usage` and call `_update_langsmith_usage(...)`, add a fire-and-forget
`usage_recorder.record(...)`:

```python
# inside generate_response / generate_structured_response, after a successful call:
usage = getattr(response, "usage", None)
if usage:
    await usage_recorder.record(
        model=self._model, provider=self._provider, profile=self.profile,
        prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
        latency_ms=elapsed_ms, is_retry=attempt_index > 0, token_source="measured")
else:  # provider omitted usage → fall back to the EXISTING estimator
    est = self._estimate_tokens(messages, ...)
    await usage_recorder.record(..., token_source="estimated")
```

For `stream_response` (chat), record when the stream closes using the `include_usage` final chunk. The three profiles
(`main`/`chat`/`critic`) already carry their model/provider, so `profile`, `model`, `provider` are available on `self`.

### 3.3 Run tracking (`services/run_tracker.py`) + `ComplianceEngine`

Wrap the engine's existing "claim submission → run graph → finally reset context" flow
(`backend/app/services/agents/compliance/engine.py`):

```python
# In analyze_submission, right after claiming the submission under the row lock:
run = await run_tracker.open(db, submission_id=sid, user=current_actor, session_id=session_id,
                             trigger_source=trigger_source)   # computes run_number, is_rerun
set_usage_context(submission_id=str(sid), run_id=str(run.id), feature="analysis")
try:
    final_state = await run_workflow(...)          # every LLM call inside now attributes to this run
    ...
finally:
    await run_tracker.close(db, run, final_state)  # status, degraded_reason, duration, SUM tokens/cost, check_id
    GraphContext.reset(...)                         # EXISTING behaviour preserved
```

`run_tracker.open` computes `run_number = SELECT count(*)+1 FROM analysis_runs WHERE submission_id=…` and
`is_rerun = run_number > 1`. `run_tracker.close` rolls up `SUM(prompt_tokens/…/total_cost_usd)` from `llm_usage_events WHERE
run_id = run.id`, sets `finished_at`/`duration_ms`, and links `compliance_check_id` if one was persisted (null otherwise → the
fail-closed case, still fully costed). It also emits `analysis_started` / `analysis_finished` (`+ analysis_rerun` if
`run_number>1`) audit events.

**Who is `current_actor`?** The analyze routes gain `user = Depends(require("analysis:run"))`; the engine call passes that user
(and `request.state.session_id`) through.

---

## 4. Audit service (`services/observability/audit.py`)

```python
async def record(event_type, *, actor=None, request=None, target_type=None, target_id=None,
                 before=None, after=None, metadata=None):
    try:
        ip = extract_client_key(request, trust_forwarded_for=settings.trust_forwarded_for) if request else None
        await insert_audit_event(event_type=event_type,
            actor_user_id=str(actor.id) if actor else None,
            actor_role=getattr(actor, "role", None),
            actor_ip=ip, session_id=getattr(getattr(request,'state',None),'session_id',None),
            target_type=target_type, target_id=target_id, before=before, after=after, metadata=metadata)
    except Exception as e:
        logger.warning("audit: dropped event %s: %s", event_type, e)
```

Call sites: `auth.py` (login/logout/failed), `rules.py` (rule_* with before/after), `admin_console.py` (user_*), the run
tracker (analysis_*), the feedback route (feedback_submitted).

---

## 5. Wiring RBAC into existing routers

Add the dependency to each existing route. Examples:

```python
# rules.py
@router.patch("/{rule_id}")
async def update_rule(rule_id, body, request: Request, user = Depends(require("rules:write")), db = Depends(get_db)):
    old = await db.get(Rule, rule_id)
    ...existing versioned update...
    await audit.record("rule_updated", actor=user, request=request, target_type="rule",
                       target_id=str(new.id), before=serialize_rule(old), after=serialize_rule(new),
                       metadata={"version": new.version})

# compliance.py
@router.post("/analyze/{submission_id}/stream")
async def analyze_stream(submission_id, request: Request,
                         user = Depends(require("analysis:run")),
                         _rl = Depends(llm_rate_limit), _bg = Depends(llm_budget_guard)):
    ...
```

The paid-endpoint guards (`llm_rate_limit`, `llm_budget_guard`) **stay** — RBAC stacks on top of them. Read-only rule GETs use
`require("rules:read")` (all roles). `super_admin` is simply absent from `analysis:run`/`chat:use`, so the API blocks grading for
that role even if the UI were bypassed.

---

## 6. Super-Admin console API (`api/routes/admin_console.py`)

Prefix `/super_admin` (or `/admin-api` if you prefer to keep the frontend route name separate from the API path). Every route
`Depends(require("console:view"))` at minimum, with finer permissions per action.

| Method · Path | Permission | Returns |
|---|---|---|
| `GET /super_admin/users` | `users:manage` | user list + status + last_login + registered_ip |
| `POST /super_admin/users` | `users:manage` | create user (username, temp pw, ip, role) |
| `PATCH /super_admin/users/{id}` | `users:manage` | update ip/role/is_active; reset password |
| `POST /super_admin/users/{id}/force-logout` | `users:manage` | revoke all sessions |
| `GET /super_admin/usage/summary` | `usage:view` | per-user tokens+cost (date range) |
| `GET /super_admin/usage/by-document` | `usage:view` | per-submission input/output tokens + cost + #runs |
| `GET /super_admin/usage/timeseries` | `usage:view` | daily cost/tokens |
| `GET /super_admin/runs` | `usage:view` | analysis_runs list (filter user/date/status) |
| `GET /super_admin/submissions/{id}/runs` | `usage:view` | per-doc re-run detail |
| `GET /super_admin/sessions` | `usage:view` | active + historical sessions, durations |
| `GET /super_admin/audit` | `audit:view` | audit_events feed (filters) |
| `GET /super_admin/rules/audit` | `audit:view` | rule-change timeline (who changed what) |
| `GET /super_admin/rules` / `PATCH …` | `rules:write` | super-admin rule monitoring + editing (audited) |
| `GET /super_admin/export/usage.csv` | `usage:view` | CSV export |

These are thin handlers over the SQL rollups in [02 §7](./02-token-cost-monitoring.md).

---

## 7. Config additions (`backend/app/config.py`)

```python
# --- Auth ---
auth_enabled: bool = True
auth_ip_binding_mode: str = "strict"          # strict | cidr | list | log_only  (Decision D1)
session_absolute_ttl_seconds: int = 8 * 3600
session_idle_ttl_seconds: int = 60 * 60
login_max_attempts: int = 5
login_lockout_seconds: int = 900
session_cookie_secure: bool = True            # requires TLS at nginx
# bootstrap (seed script reads these)
super_admin_username: str = ""
super_admin_password: str = ""
super_admin_ip: str = ""
# --- Cost model (Decision D6) ---
llm_prices: dict = {}                          # {model: {"input": x, "output": y}} per 1k tokens; fill from contract
llm_price_currency: str = "USD"
```

Also flip `trust_forwarded_for: bool = True` **in the deployment env** (behind nginx) so IP binding reads the real client IP —
document this in `.env.prod.example`.

---

## 8. `main.py` wiring

- Register `auth.router` and `admin_console.router` alongside the existing `app.include_router(...)` block.
- Add the auth middleware after CORS.
- Tighten `settings.api_cors_origins` to the UAT origin(s) only (drop the localhost dev entries in prod).
- Add the new models to the lifespan import block (registration parity for Alembic).

---

## 9. Dependencies to add (`backend/requirements.txt`)

- `argon2-cffi` (password hashing) — or `passlib[bcrypt]`.
- Everything else (FastAPI, Redis client, SQLAlchemy, tiktoken) is already present.

---

## 10. Tests (mirror the existing `backend/tests/` layout)

- `tests/auth/test_passwords.py` — hash/verify round-trip.
- `tests/auth/test_ip_binding.py` — strict/cidr/list/log_only matrix, XFF handling.
- `tests/auth/test_rbac_matrix.py` — every (role × permission) grant/deny.
- `tests/observability/test_cost.py` — `compute_cost` math incl. unknown-model default.
- `tests/observability/test_usage_context.py` — ContextVar propagates across `asyncio.gather`.
- `tests/services/test_run_tracker.py` — run_number/is_rerun; **fail-closed run still records tokens/cost** (the key case).
- `tests/api/test_auth_routes.py`, `tests/api/test_admin_console.py` — endpoint auth + shapes.
- Regression: existing analyze/chat/rules tests updated to pass an authenticated user.
