# 07 · Workflow Diagrams

Mermaid diagrams for every flow in this plan. (GitHub, VS Code Mermaid preview, and most Markdown viewers render these.)

---

## 1. System context — where the new pieces sit

```mermaid
flowchart TB
    subgraph Client["Browser (employee, on corporate Wi-Fi)"]
        LOGIN["/login"]
        WS["Workspace UI (user/admin)"]
        SA["/super_admin console"]
    end
    subgraph Proxy["nginx (sets X-Forwarded-For)"]
    end
    subgraph API["FastAPI"]
        MW["Auth middleware<br/>(load session, bind usage_context, refresh last_seen)"]
        AUTH["/auth/*"]
        CONSOLE["/super_admin/* (admin API)"]
        APP["existing routes<br/>submissions/compliance/rules/chat…<br/>+ require(permission)"]
    end
    subgraph SVC["Services"]
        SESS["Session store (Redis)"]
        UR["usage_recorder + cost"]
        RT["run_tracker"]
        AUD["audit service"]
        LLM["LLMService (real token usage)"]
    end
    subgraph DB["PostgreSQL"]
        T1["users (+auth cols)"]
        T2["user_sessions"]
        T3["analysis_runs"]
        T4["llm_usage_events"]
        T5["audit_events"]
    end

    LOGIN & WS & SA --> Proxy --> MW
    MW --> AUTH & CONSOLE & APP
    AUTH --> SESS
    AUTH --> AUD
    APP --> RT --> LLM --> UR
    APP --> AUD
    UR --> T4
    RT --> T3
    AUD --> T5
    SESS -. mirror .-> T2
    CONSOLE --> DB
```

---

## 2. Login (three-factor: username + password + IP)

```mermaid
sequenceDiagram
    actor U as User
    participant FE as Frontend (/login)
    participant NX as nginx
    participant API as /auth/login
    participant DB as Postgres
    participant R as Redis
    U->>FE: username + password (NOT ip)
    FE->>NX: POST /auth/login
    NX->>API: + X-Forwarded-For: real client ip
    API->>DB: get user by username
    alt not found / inactive / bad password
        API->>DB: INSERT audit_events(login_failed)
        API-->>FE: 401 invalid credentials
    else IP not allowed (strict mode)
        API->>DB: INSERT audit_events(login_failed, ip_mismatch)
        API-->>FE: 403 "ask an admin to update your IP"
    else success
        API->>R: SET session:{sid} bound to (user,ip,ua)
        API->>DB: INSERT user_sessions(login_at)
        API->>DB: INSERT audit_events(login)
        API-->>FE: 200 {role, must_change_password} + Set-Cookie rca_session (httpOnly)
    end
    FE->>FE: route by role (super_admin→/super_admin, else →/); force pw change if needed
```

---

## 3. User provisioning (admin/super-admin creates an account)

```mermaid
sequenceDiagram
    actor A as Admin / Super-admin
    participant FE as Console › Users
    participant API as POST /super_admin/users
    participant DB as Postgres
    A->>FE: username, temp password, registered IP, role
    FE->>API: create user (session cookie)
    API->>API: require(users:manage) ; admin may create role=user only
    API->>DB: INSERT users(password_hash, registered_ip, is_active, must_change_password=true, created_by=A)
    API->>DB: INSERT audit_events(user_created, after={username,role,ip})
    API-->>FE: 201 created
    Note over A,DB: First login forces password change → must_change_password=false
```

---

## 4. Token & cost capture (the money pipeline)

```mermaid
flowchart LR
    REQ["Authenticated request"] --> MW["middleware:<br/>usage_context(user_id, session_id)"]
    MW --> H["route handler:<br/>usage_context(feature, submission_id)"]
    H --> ENG["ComplianceEngine:<br/>open analysis_runs → usage_context(run_id)"]
    ENG --> G["graph nodes: grade / sweep / critic / disclosure"]
    G --> LLM["LLMService call"]
    LLM --> USE{"response.usage present?"}
    USE -->|yes| REC["usage_recorder.record(measured)"]
    USE -->|no| EST["estimate via tiktoken → record(estimated)"]
    REC & EST --> COST["compute_cost(model, in, out)"]
    COST --> EV["INSERT llm_usage_events<br/>(user, session, submission, run, in/out tok, $)"]
    ENG --> CLOSE["run_tracker.close:<br/>SUM events → analysis_runs rollup + finished_at"]
    EV -. SUM .-> CLOSE
    CLOSE --> CON["Super-Admin console rollups"]
    EV --> CON
```

Key property: **every billed call** (including failover retries and schema-correction retries) writes its own event, so the
per-document/per-user/per-run cost reflects real spend — not a single "happy path" estimate.

---

## 5. Re-run tracking

```mermaid
sequenceDiagram
    actor U as User
    participant API as /compliance/analyze/{id}
    participant RT as run_tracker
    participant DB as Postgres
    U->>API: analyze (again) submission X
    API->>API: require(analysis:run)
    API->>RT: open(submission=X, user=U, source=stream)
    RT->>DB: run_number = count(analysis_runs where submission=X)+1
    RT->>DB: INSERT analysis_runs(run_number=n, is_rerun=(n>1), status=running)
    RT->>DB: INSERT audit_events(analysis_started [+ analysis_rerun if n>1])
    Note over API: graph runs; LLM calls attributed to this run_id
    alt run persists a grade
        API->>DB: ComplianceCheck + Violations (status=analyzed)
        RT->>DB: close: status=completed, link check_id, SUM tokens/cost
    else fail-closed (needs_review/failed) — NO ComplianceCheck
        RT->>DB: close: status=needs_review, check_id=NULL, degraded_reason, SUM tokens/cost
    end
    RT->>DB: INSERT audit_events(analysis_finished, cost)
```

> The fail-closed branch is why runs are tracked separately from `compliance_checks`: **no grade persisted, but tokens still
> spent and fully costed.**

---

## 6. Rule change → audit (who changed what)

```mermaid
sequenceDiagram
    actor A as Admin / Super-admin
    participant API as PATCH /rules/{id}
    participant DB as Postgres
    A->>API: edit rule (severity / text)
    API->>API: require(rules:write)
    API->>DB: read old rule (v_k)
    alt content change
        API->>DB: INSERT new rule row (v_k+1, created_by=A), old.superseded_by=new, old.is_active=false
    else activate/deactivate only
        API->>DB: UPDATE is_active in place
    end
    API->>DB: INSERT audit_events(rule_updated, before=v_k, after=v_k+1, actor=A, ip)
    API-->>A: 200
    Note over DB: Console renders per-rule timeline from rule_* events + version chain
```

---

## 7. RBAC decision (per request)

```mermaid
flowchart TD
    R["Incoming request"] --> C{"valid session cookie?"}
    C -->|no| L["401 → /login"]
    C -->|yes| IP{"bound IP matches?<br/>(unless log_only)"}
    IP -->|no| X["401 session ended"]
    IP -->|yes| P{"role_has(role, permission)?"}
    P -->|no| D["403 + audit(authz_denied)"]
    P -->|yes| OK["handler runs<br/>(paid routes also pass rate-limit + budget guards)"]
```

---

## 8. Session lifecycle & time tracking

```mermaid
stateDiagram-v2
    [*] --> Active: login (Redis session + user_sessions row)
    Active --> Active: request / heartbeat → last_seen_at refreshed
    Active --> Closed: POST /auth/logout (duration = logout_at - login_at)
    Active --> Expired: idle > idle_ttl OR now > absolute_ttl (swept → duration from last_seen)
    Closed --> [*]
    Expired --> [*]
```

---

## 9. Role → surface routing (frontend)

```mermaid
flowchart TD
    OPEN["User opens site"] --> ME{"GET /auth/me"}
    ME -->|no session| LOGIN["/login"]
    ME -->|must_change_password| CPW["/account/change-password"]
    ME -->|role=user| WU["Workspace (rules read-only)"]
    ME -->|role=admin| WA["Workspace (rules editable + generate)"]
    ME -->|role=super_admin| SAC["/super_admin console (no grading)"]
```
