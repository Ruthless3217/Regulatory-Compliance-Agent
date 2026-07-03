# 08 · Security Hardening

The Wi-Fi boundary is **one** layer, not the whole defense. An internal UAT still faces a malicious/curious insider, a stolen
laptop, a compromised machine on the same LAN, or a mis-scoped firewall rule later exposing the VM. This section lists concrete
protections — marking what the codebase **already** does vs. what to **add**.

---

## 1. Threat model (concise)

| Threat | Vector | Mitigation |
|--------|--------|-----------|
| Unauthorized use | anyone on the LAN reaches the VM | login required; per-request auth; IP binding |
| Credential theft | shoulder-surf, shared password | Argon2id hashing, forced rotation, lockout, session IP-binding |
| Session hijack | stolen `rca_session` cookie used elsewhere | `httpOnly`+`Secure`+`SameSite=Strict`; session bound to IP + UA; short TTL; revocation |
| Privilege escalation | `user` calls admin/console API directly | server-side `require(permission)` on every route (UI hiding is not the gate) |
| Rule tampering (untraceable) | someone edits a rule | RBAC (`rules:write`) + append-only `rule_updated` audit + version chain |
| Cost blindness / abuse | someone hammers analysis / re-runs | per-user usage ledger + existing rate-limit + global budget; console alerts |
| Audit tampering | actor deletes their tracks | append-only `audit_events` (trigger + restricted DB role) |
| IP spoofing | forged `X-Forwarded-For` | trust XFF **only** from the known proxy; validate proxy chain |
| Injection via content | malicious copy in a submission/chat | **already** fenced with per-call UUID (`chat.py`, `preprocessing_service.py`); path-traversal guards on ingest |
| Secret leakage in logs | passwords/PII written to disk | **already** PII redaction (`pii.py`, `redaction.py`); add password field to denylist; keep file logging off in prod |

---

## 2. Authentication hardening

- **Password hashing:** Argon2id (memory-hard) or bcrypt cost ≥ 12. Never store or log plaintext/hash.
- **Password policy:** ≥ 12 chars, ≠ username, forced change on admin-set passwords (`must_change_password`), optional rotation
  via `password_updated_at`.
- **Brute-force lockout:** reuse the `FixedWindowLimiter` pattern from `backend/app/api/rate_limit.py`, keyed on
  `username`+`ip`. After `login_max_attempts` (5) within `login_lockout_seconds` (900) → 429. Log `login_locked`.
- **Generic errors:** identical 401 for unknown-user vs. bad-password vs. disabled → no user enumeration.
- **No open bootstrap:** first super-admin only via the seed script / env (see [01 §4](./01-auth-rbac-design.md)); there is no
  public "register" endpoint.

## 3. Session hardening

- Cookie: `httpOnly` (JS can't read it), `Secure` (TLS only), `SameSite=Strict` (blocks cross-site sends). No role/PII in the
  cookie — it's an opaque 256-bit id.
- **Bind session to IP + User-Agent**; re-check on every request. Mismatch → invalidate (`session_ip_change` audit).
- **Absolute TTL** (8h) + **idle TTL** (60m sliding). **Server-side revocation** (disable user / force-logout deletes all
  `session:*` keys).
- **Fail closed for auth if Redis is down** (503 on login), unlike the rate-limiter which fails open. Auth must never silently
  degrade to "no session required."

## 4. CSRF

Cookie-based auth is CSRF-exposed. Defenses (use both):
1. `SameSite=Strict` on `rca_session` (primary; blocks cross-origin form/GET-driven sends).
2. A **double-submit CSRF token** for state-changing requests: issue a non-`httpOnly` `csrf` cookie at login; the frontend
   echoes it in an `X-CSRF-Token` header; the backend compares. Enforce on all `POST/PATCH/DELETE`.
3. Reject requests whose `Origin`/`Referer` isn't the app origin.

## 5. CORS & transport

- **Tighten CORS** (`main.py:99-105`): the current `api_cors_origins` includes localhost dev entries. In prod, restrict to the
  exact UAT origin(s) and keep `allow_credentials=True` (required for the cookie) — but `allow_credentials=True` is incompatible
  with `allow_origins=["*"]`, so origins must be explicit.
- **TLS everywhere:** terminate HTTPS at nginx so `Secure` cookies work and credentials aren't sniffable on the LAN. Redirect
  HTTP→HTTPS. (Self-signed/internal-CA cert is fine for UAT; the backend already handles corporate CAs.)
- **HSTS**, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, a restrictive `Content-Security-Policy` at nginx.

## 6. IP / proxy trust

- Set `trust_forwarded_for=True` **only** because a trusted nginx sits in front and sets `X-Forwarded-For`. If the app is ever
  exposed directly, this must revert to `False` (XFF becomes client-controlled and spoofable).
- Prefer having nginx set a **single, sanitized** client-IP header (strip inbound XFF, then append the real peer) so a client
  can't inject extra hops. Consider `X-Real-IP` from nginx and read that specifically.

## 7. Authorization hardening

- Central `ROLE_PERMISSIONS` map; **no numeric "level ≥"** shortcut (super-admin ≠ superset of admin's grading rights).
- Deny-by-default: any route without an explicit permission dependency should be unreachable or return 403.
- Log every 403 as `authz_denied` (helps spot probing).
- The `/super_admin` route returns **404** (not 403) to an unauthorized caller so the route isn't advertised.

## 8. Audit immutability

- `audit_events` and (ideally) `llm_usage_events`/`analysis_runs` are **append-only from the app**: the application DB user gets
  `INSERT, SELECT` only on these tables; `UPDATE/DELETE` reserved to the migration/owner role.
- Add the `BEFORE UPDATE OR DELETE` trigger from [04 §6](./04-database-schema.md) as belt-and-suspenders.
- Backups already exist (daily `db-backup` sidecar, 14-day retention) — audit rows are captured there too.

## 9. Secrets & logging

- Add the password/hash field names to the redaction denylists in `pii.py` / `redaction.py`.
- Keep verbose LLM file logging **off** in prod (the JSON log is capped at 100 entries and PII-masked, but least-logging is
  safest for content under review).
- Store `SUPER_ADMIN_PASSWORD` and price/keys via env/secret store, never in the repo. `.env.prod.example` documents them as
  placeholders only.

## 10. Rate limiting & cost guards (already present — keep + extend)

- Keep `Depends(llm_rate_limit)` + `Depends(llm_budget_guard)` on paid endpoints. Now that requests are authenticated, key the
  limiter on **user id** (more precise than IP), while retaining IP as a fallback.
- Add **per-user / per-day cost alerts** in the console (e.g. flag a user > $X/day) and optionally a soft per-user token cap that
  routes overage to `needs_review` instead of spending.

## 11. Operational

- **Least-privilege DB roles** (app role vs. migration/owner role).
- **Dependency hygiene:** pin and periodically update `argon2-cffi`, FastAPI, etc.
- **Session/audit monitoring:** surface repeated `login_failed`, `session_ip_change`, and `authz_denied` in the console.
- **Penetration sanity checks:** verify a `user` token cannot hit `/rules` mutations, `/super_admin/*`, or another user's
  submission; verify a stolen cookie fails from a different IP; verify audit rows can't be updated.

---

## 12. Hardening checklist (copy into the PR)

- [ ] Argon2id password hashing; no plaintext/hash logged
- [ ] Forced first-login password change; lockout after N failures
- [ ] Opaque `httpOnly`+`Secure`+`SameSite=Strict` session cookie; IP+UA bound; TTL + idle timeout; revocation
- [ ] Auth fails **closed** when Redis unavailable
- [ ] CSRF double-submit token on all mutations + Origin check
- [ ] CORS locked to UAT origin; TLS at nginx; security headers + HSTS
- [ ] `trust_forwarded_for` on **only** behind the trusted proxy; sanitized client-IP header
- [ ] `require(permission)` on **every** route; deny-by-default; `/super_admin` → 404 when unauthorized
- [ ] `audit_events` append-only (DB perms + trigger); least-privilege app DB role
- [ ] Per-user rate-limit + budget; cost alerts in console
- [ ] Seed-script bootstrap for first super-admin; no open register endpoint
- [ ] Password/secret fields added to log redaction denylists
