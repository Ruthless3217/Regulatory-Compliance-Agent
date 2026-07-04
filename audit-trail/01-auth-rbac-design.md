# 01 · Authentication & RBAC Design

This file specifies **how a person proves who they are** (authentication) and **what each role may do** (authorization / RBAC).

---

## 1. Authentication model — IP + username + password

Because the app is Wi-Fi-only and there is no identity provider, we implement a **self-contained credential + IP-binding**
scheme. There is no self-service signup — **accounts are provisioned by an admin/super-admin**.

### 1.1 Account provisioning (admin action)

An admin (or super-admin) creates an account with three inputs:

- **name / username** — the login handle (unique, e.g. `rohit.sharma`). Distinct from the existing `email` column.
- **password** — set by the admin; the user is forced to change it on first login (`must_change_password = true`).
- **registered IP** — the employee's machine IP on the corporate network (e.g. `10.20.14.37`). Stored on the user record.
- **role** — `user` / `admin` / (`super_admin` only settable by a super-admin).

The plaintext password is **never stored**. It is hashed with **Argon2id** (recommended) or bcrypt and only the hash is
persisted (see §1.4). See [04 · DB schema](./04-database-schema.md) for the exact `users` columns added.

### 1.2 Login flow (verifies all three factors)

```
1. User opens the site  →  Next.js middleware sees no session cookie  →  redirect to /login
2. User submits { username, password }        (IP is taken from the request, NOT typed by the user)
3. Backend POST /auth/login:
     a. Look up user by username. Not found OR is_active = false  → 401 (generic "invalid credentials")
     b. verify_password(password, user.password_hash)            → mismatch → 401 + record login_failed audit event
     c. client_ip = extract_client_key(request, trust_forwarded_for=settings.trust_forwarded_for)
        IF ip_binding_enabled AND client_ip NOT allowed for user → 403 "IP not recognised — contact an admin"
                                                                    + record login_failed(reason=ip_mismatch)
     d. Brute-force check: too many recent failures for this username/IP → 429 (temporary lockout)
     e. SUCCESS → create server-side session (Redis) bound to (user_id, client_ip, user_agent)
                → set httpOnly Secure SameSite=Strict cookie `rca_session`
                → open a user_sessions row (login_at = now)
                → record login audit event
                → return { role, must_change_password }
4. Frontend routes by role:  super_admin → /super_admin ;  user/admin → /  (workspace)
   If must_change_password → force /account/change-password first.
```

> **Why IP is server-derived, not typed:** the user never enters their IP. The backend reads it from the connection (via the
> trusted proxy's `X-Forwarded-For`, reusing `extract_client_key`). This prevents a user from spoofing another's mapped IP
> through the form.

### 1.3 Session management (recommended: Redis server-side sessions)

- On login, generate a 256-bit random opaque token `sid`. Store in Redis:
  `session:{sid} → { user_id, role, ip, user_agent, created_at, last_seen_at, must_change_password }` with a **TTL** (e.g.
  8h absolute) and a **sliding idle timeout** (e.g. 60 min — refreshed on each authenticated request).
- The browser holds only the opaque `sid` in an `httpOnly` `Secure` `SameSite=Strict` cookie — no role or PII in the cookie.
- Every authenticated request: middleware loads the session, **re-checks the bound IP** (defense against cookie theft from a
  different machine), refreshes `last_seen_at`, and mirrors `last_seen_at` onto the `user_sessions` row (throttled, e.g. at
  most once / 30s) so session-time is measurable.
- **Logout** (`POST /auth/logout`): delete the Redis key, set `user_sessions.logout_at` + compute `duration_seconds`, record a
  `logout` audit event.
- **Revocation**: an admin disabling a user, or a super-admin "force logout", deletes all `session:*` keys for that user.
- **Redis-down behaviour (fail-closed for auth):** if Redis is unavailable, new logins fail with a clear 503 rather than
  silently falling back to an unauthenticated state. *(Contrast with rate-limiting, which fails **open** to an in-process
  limiter — auth must not.)*

> **Alternative (D2): signed JWT.** Stateless, no Redis dependency, but: (a) revocation needs a denylist anyway, and (b) live
> session-time tracking is awkward. Given Redis is already required and session-time is a first-class requirement, server-side
> sessions win. If JWT is chosen later, keep the same `user_sessions` table for time tracking and add a `jti` denylist.

### 1.4 Password storage & policy

- Hash with **Argon2id** (`argon2-cffi`) — memory-hard, modern. bcrypt (`passlib[bcrypt]`) is an acceptable fallback.
- Never log the password or hash (the existing `services/pii.py` / `redaction.py` already scrub logs; add the password field
  to their denylist to be safe).
- Policy: min 12 chars, not equal to username, `must_change_password` on admin-set passwords, N-strike lockout (see 08).
- Store `password_updated_at` for audit and optional rotation.

### 1.5 IP binding — the DHCP problem (Decision D1)

Corporate DHCP can reassign IPs, which would lock users out. Options, in order of recommendation:

1. **Single mapped IP, admin-updatable (recommended default).** Simple and matches the ask ("admin will add his ip"). On
   mismatch, deny with an actionable message; an admin re-maps in one click from the console. Pair with **long DHCP
   reservations** for these machines if possible.
2. **Per-user subnet / CIDR** (`allowed_cidr`, e.g. `10.20.14.0/24`). Tolerates DHCP within a VLAN while still constraining to
   the office network. Good balance if IPs churn a lot.
3. **List of allowed IPs per user** (`allowed_ips JSONB`). Explicit but higher admin overhead.
4. **IP as a logged signal only** (not a hard gate). Weakest; use only if churn makes hard-gating impractical. The console
   still shows the login IP; anomalies are flagged, not blocked.

The schema in [04](./04-database-schema.md) supports all of these (`registered_ip` + optional `allowed_cidr` + `allowed_ips`),
gated by a single config flag `AUTH_IP_BINDING_MODE = strict | cidr | list | log_only`.

---

## 2. RBAC — authorization model

### 2.1 Roles

`user` < `admin` < `super_admin` — but note **super_admin is not a strict superset of admin's grading rights** (super-admin does
*not* grade documents). So enforce **explicit permission grants per endpoint**, not a numeric "level ≥" comparison.

### 2.2 Permission catalogue

Define named permissions and map roles → permissions once, centrally.

| Permission | user | admin | super_admin |
|------------|:----:|:-----:|:-----------:|
| `submission:create` | ✅ | ✅ | ❌ |
| `submission:read` | ✅ | ✅ | ❌ |
| `submission:delete` | ✅(own)¹ | ✅ | ❌ |
| `analysis:run` (incl. re-run, sync/async/stream) | ✅ | ✅ | ❌ |
| `chat:use` (chat / quote / rewrite) | ✅ | ✅ | ❌ |
| `comparison:use` | ✅ | ✅ | ❌ |
| `dashboard:view` | ✅ | ✅ | ❌ |
| `knowledgebase:view` | ✅ | ✅ | ✅ |
| `rules:read` | ✅ | ✅ | ✅ |
| `rules:write` (create/update/deactivate/delete) | ❌ | ✅ | ✅ |
| `rules:generate` (LLM extraction) | ❌ | ✅ | ✅ |
| `feedback:submit` | ✅ | ✅ | ✅ |
| `console:view` (super-admin console) | ❌ | ❌ | ✅ |
| `users:manage` | ❌ | ✅² | ✅ |
| `audit:view` | ❌ | ❌ | ✅ |
| `usage:view` (token/cost/session) | ❌ | ❌ | ✅ |

¹ Or restrict delete to admins entirely — cheap simplification. ² Per D3, admin may create only `user` accounts.

### 2.3 Endpoint → permission map (backend enforcement)

Every route gets a `require(permission)` dependency. Current routers map as:

| Router / route | Required permission |
|----------------|---------------------|
| `POST /submissions`, `GET /submissions*`, `DELETE /submissions/{id}` | `submission:*` (user, admin) |
| `POST /compliance/analyze/{id}`, `/sync`, `/stream` | `analysis:run` (user, admin) |
| `GET /compliance/results/{id}`, `/check/{id}` | `submission:read` |
| `POST /compliance/violations/{id}/feedback` | `feedback:submit` (all roles) |
| `POST /chat`, `/chat/quote-violation`, `/chat/suggest-rewrite` | `chat:use` (user, admin) |
| `GET /dashboard/*` | `dashboard:view` (user, admin) |
| `GET /rules`, `GET /rules/{id}` | `rules:read` (all roles) |
| `POST /rules`, `PATCH /rules/{id}`, `DELETE /rules/{id}` | `rules:write` (**admin, super_admin only**) |
| `POST /rules/generate-from-document` | `rules:generate` (**admin, super_admin only**) |
| `POST /comparisons`, `GET/DELETE /comparisons/*` | `comparison:use` (user, admin) |
| `GET /knowledge-base/*` | `knowledgebase:view` (all) |
| `POST /knowledge-base/ingest` | `usage:view`/admin-op (super_admin; or ops-only) |
| **NEW** `/auth/*` | public (login) / self (logout, me, change-password) |
| **NEW** `/super_admin/*` (aka `/admin-api/*`) | `console:view`, `users:manage`, `audit:view`, `usage:view` |

**Enforcement is server-side and authoritative.** The frontend also hides forbidden UI (see [06](./06-frontend-implementation.md)),
but hiding a button is UX, not security — the API rejects unauthorized calls with **403** regardless.

### 2.4 The `/super_admin` URL

- The console lives at `…/super_admin` (respecting `NEXT_PUBLIC_BASE_PATH`, so e.g. `/compliance/super_admin`).
- It is **not linked** from any normal navigation. It is reachable only by typing the URL and only renders for a `super_admin`
  session; any other (or no) session → redirect to `/login` (or a 404-style "not found" to avoid advertising the route).
- The backend admin API is enforced independently of the frontend route.

---

## 3. User provisioning & lifecycle

```
create user   → admin/super-admin sets username, temp password, registered IP, role
                → users row (is_active=true, must_change_password=true, created_by=<actor>)
                → audit event user_created
first login   → forced password change → must_change_password=false
disable user  → is_active=false → all sessions revoked → audit event user_disabled
update IP     → registered_ip changed → audit event user_ip_updated (before/after)
reset password→ admin sets new temp password → must_change_password=true → sessions revoked
change role   → super_admin only → audit event user_role_changed (before/after)
```

## 4. Bootstrapping the first super-admin

There must be no public "create the first admin" endpoint (that would be an open backdoor). Instead:

- A **one-shot seed script** `backend/scripts/seed_super_admin.py` (run once via `docker exec`, mirroring the existing
  `seed_rules` / `ingest_knowledge_base` script pattern) reads `SUPER_ADMIN_USERNAME`, `SUPER_ADMIN_PASSWORD`,
  `SUPER_ADMIN_IP` from env, creates the account with `role=super_admin`, `must_change_password=true`, and exits. It is a no-op
  if a super-admin already exists.
- Alternatively, the backend lifespan (`main.py`) can create it on first boot if `SUPER_ADMIN_*` env vars are present and no
  super-admin exists. The seed-script approach is cleaner and auditable.

## 5. Failure modes & responses

| Situation | HTTP | User-facing message | Audit event |
|-----------|:----:|---------------------|-------------|
| Unknown username / wrong password | 401 | "Invalid username or password." (generic — no user enumeration) | `login_failed` |
| Correct creds, wrong IP (strict mode) | 403 | "This device isn't recognised for your account. Ask an admin to update your IP." | `login_failed(ip_mismatch)` |
| Too many failures | 429 | "Too many attempts. Try again in N minutes." | `login_locked` |
| Disabled account | 401 | generic invalid-credentials (don't reveal disabled state) | `login_failed(disabled)` |
| Valid session, IP changed mid-session | 401 + clear cookie | "Session ended — please log in again." | `session_ip_change` |
| Redis down at login | 503 | "Sign-in is temporarily unavailable." (fail closed) | `auth_infra_error` |
| Authenticated but insufficient role | 403 | "You don't have access to this." | `authz_denied` |

## 6. What this reuses vs. adds

- **Reuses:** `extract_client_key()` + `trust_forwarded_for` for IP; `get_redis()` for sessions; the `users` table + `role`
  column; the existing FastAPI `Depends(...)` pattern (like `llm_rate_limit`).
- **Adds:** password hashing, `/auth/*` routes, session middleware, `require(permission)` dependency factory, the
  permission→role map, and the `/super_admin` admin API (detailed in [05](./05-backend-implementation.md)).
