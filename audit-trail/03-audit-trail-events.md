# 03 · Audit Trail & Event Log

A tamper-evident record of **who did what, when, from where** — covering security events (logins), money events (runs), and
governance events (rule changes). This is the "audit trail" the directory is named for.

---

## 1. Design principles

- **Append-only.** The application only ever `INSERT`s into `audit_events`. No `UPDATE`/`DELETE` from app code; enforced by a
  restricted DB role and a trigger (see [08](./08-security-hardening.md) §Audit immutability).
- **Actor + context on every row.** `actor_user_id`, `actor_role`, `actor_ip`, `session_id`, `created_at` (UTC).
- **Before/after for state changes.** Rule and user mutations store `before`/`after` JSONB snapshots so a reviewer sees exactly
  what changed.
- **Never store secrets.** Passwords/hashes are never written to the audit log (redaction denylist).
- **Decouple from the request path.** Writing an audit row must not fail the user's action (best-effort insert + error log);
  but security-critical events (login/logout/authz-deny) should be written synchronously before responding.

## 2. Event taxonomy

`event_type` values grouped by domain:

### Auth / session
| event_type | When | Notable fields |
|------------|------|----------------|
| `login` | successful login | ip, user_agent |
| `login_failed` | bad password / IP mismatch / disabled | `reason` (bad_password \| ip_mismatch \| disabled) |
| `login_locked` | brute-force lockout triggered | attempt_count, window |
| `logout` | explicit logout | session_id |
| `session_expired` | idle/absolute timeout | session_id |
| `session_ip_change` | bound IP changed mid-session | old_ip, new_ip |
| `password_changed` | user or admin changed a password | by_self (bool) |

### User management
| event_type | When | before/after |
|------------|------|--------------|
| `user_created` | admin provisions an account | after = {username, role, ip} |
| `user_updated` | profile/IP edited | before/after diff |
| `user_ip_updated` | registered IP re-mapped | before/after IP |
| `user_role_changed` | role changed (super-admin only) | before/after role |
| `user_disabled` / `user_enabled` | activation toggled | — |
| `user_password_reset` | admin reset a password | — (no secret) |

### Rules governance (the "who changed what rule" requirement)
| event_type | When | before/after |
|------------|------|--------------|
| `rule_created` | `POST /rules` or generation accept | after = full rule |
| `rule_updated` | `PATCH /rules/{id}` content change (new version) | before = old version, after = new version, `new_rule_id`, `version` |
| `rule_activated` / `rule_deactivated` | `is_active` toggled | before/after is_active |
| `rule_deleted` | `DELETE /rules/{id}` | before = deleted rule |
| `rules_generated` | `POST /rules/generate-from-document` | after = draft rule ids/count, source doc |

### Grading / money
| event_type | When | fields |
|------------|------|--------|
| `submission_created` | doc uploaded/pasted/URL | content_type, size |
| `analysis_started` | run opened | run_id, run_number, trigger_source |
| `analysis_rerun` | run_number ≥ 2 | run_id, run_number |
| `analysis_finished` | run closed | run_id, status, total_cost_usd, degraded_reason |
| `feedback_submitted` | violation accept/reject | violation_id, verdict |
| `submission_deleted` | doc removed | submission_id |

### Console / access control
| event_type | When |
|------------|------|
| `authz_denied` | authenticated user hit a forbidden route (403) |
| `console_viewed` | super-admin opened a console section (optional, for meta-audit) |
| `usage_exported` | super-admin exported a report (CSV/PDF) |

## 3. Rule-change audit — how it hooks the existing code

The rules router **already versions** rules (`PATCH /rules/{id}` creates a new row, sets `superseded_by`, bumps `version`;
`created_by` links the actor). We add audit capture at those exact points:

```python
# In backend/app/api/routes/rules.py — inside the PATCH handler, after the versioned write:
await audit.record(
    event_type="rule_updated",
    actor=current_user,                 # from require("rules:write") dependency
    request=request,
    target_type="rule", target_id=str(new_rule.id),
    before=serialize_rule(old_rule),    # the superseded version
    after=serialize_rule(new_rule),
    metadata={"version": new_rule.version, "superseded": str(old_rule.id)},
)
```

Because rules are **versioned, not mutated in place**, the audit event + the version chain together give a complete, defensible
history: the console can render a timeline per rule (v1 by admin A on date, v2 by super-admin on date, deactivated by admin B…).

**Super-admin editing rules is itself audited** — there is no bypass. The ask ("keep option for super admin to change the rules
too and monitor who changed what") is satisfied by the same hook: super-admin edits produce `rule_updated` events with
`actor_role = super_admin`.

## 4. `audit_events` table (shape)

Full DDL in [04](./04-database-schema.md). Columns:

```
id              uuid pk
event_type      varchar   (indexed)
actor_user_id   uuid  → users.id (nullable: system/anonymous)
actor_role      varchar
actor_ip        varchar
session_id      varchar   (nullable)
target_type     varchar   (rule|user|submission|session|run|null)
target_id       varchar   (nullable)
before          jsonb     (nullable)
after           jsonb     (nullable)
metadata        jsonb     (nullable)
created_at      timestamptz default now()   (indexed)
```

Indexes: `(event_type, created_at)`, `(actor_user_id, created_at)`, `(target_type, target_id)`.

## 5. Retention & volume

- Login/authz/rule events are low-volume → retain **indefinitely** (or ≥ 2 years) for governance.
- `analysis_started/finished` are higher-volume but bounded by run count → same store is fine; if it grows, partition
  `audit_events` by month.
- The high-volume, granular per-LLM-call data lives in **`llm_usage_events`** (see 02), *not* in `audit_events` — keep the audit
  log human-scale (one row per meaningful action), and the usage ledger machine-scale (one row per LLM call).

## 6. What the super-admin sees (audit views)

- **Activity feed** — reverse-chronological `audit_events`, filterable by actor, event_type, date, target.
- **Rule history** — per-rule timeline built from `rule_*` events + the version chain.
- **Login history / anomalies** — `login`, `login_failed`, `session_ip_change`, lockouts.
- **Governance report** — export of all `rule_*` and `user_*` events for a period (CSV/PDF).

## 7. Relationship to existing observability

The codebase already has `agent_executions` / `agent_traces` / `tool_invocations` (ReAct-style internal traces). Those remain
**engineering observability** (per-node, per-tool). `audit_events` is **business/security observability** (per human action).
They complement each other; the console's "runs" view can deep-link from an `analysis_run` to its `agent_executions` for
debugging, but the audit trail itself stays human-readable.
