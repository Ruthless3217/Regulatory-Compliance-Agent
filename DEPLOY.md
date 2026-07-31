# DEPLOY — Regulatory Compliance Agent (RHEL 9 VM, podman)

The VM runs **podman + podman-compose**, not docker. Checkout lives at
`/opt/Regulatory-Compliance-Agent`. Postgres / Redis / nginx are **not ours** —
they belong to the shared platform stack in `/opt/shared` and are already running.

The whole deploy is one command. Everything below is either what that command
does for you, or the two things it deliberately leaves to a human (admin
provisioning and the one-off data backfill).

```bash
cd /opt/Regulatory-Compliance-Agent
git pull
sudo bash up-shared.sh
```

---

## 0. Which compose file — read this before typing anything

Four compose files live in this repo and only one is correct here.

| File | What it is | On this VM |
|---|---|---|
| `docker-compose.shared.yml` | app containers only, joins the external `shared-network`, talks to `shared-postgres` / `shared-redis` | **THIS ONE** |
| `docker-compose.yml` | self-contained LOCAL DEV stack — brings up its **own** `compliance-postgres` with its **own** empty volume | never |
| `docker-compose.override.yml` | LOCAL DEV ONLY, and **auto-merged** by docker compose / podman-compose whenever you don't pass `-f` | never |
| `docker-compose.prod.yml` | offline "load pre-built images" variant, also bundles its own postgres | not used here |

### The trap (this has already bitten us)

Running `podman-compose up -d` with no `-f` in this directory merges
`docker-compose.yml` **+** `docker-compose.override.yml`. That:

1. starts a **second, bundled `compliance-postgres` on a fresh empty volume**, so
   the backend migrates and serves an **empty parallel database** while the real
   corpus sits untouched in `shared-postgres`. Everything "works" — zero rules,
   zero precedents, zero users;
2. pulls in the override's dev-only settings: `AUTH_IP_BINDING_MODE=log_only`,
   `SESSION_COOKIE_SECURE=false`, `LLM_BASE_URL` swung to Gemini, frontend
   remapped to host port 3001, gotenberg published on 3033.

`up-shared.sh` defends against exactly this: `drop_bundled()` force-removes
`compliance-postgres` and `compliance-redis` on every `up`, because in shared
mode the app must reach `shared-postgres` / `shared-redis` and nothing else.
If you ever see a container named `compliance-postgres` on this VM, someone
bypassed the script.

**Always pass `-f docker-compose.shared.yml` explicitly**, or just use
`up-shared.sh`, which does.

---

## 1. Prerequisites

* The shared stack is up: `shared-network`, `shared-postgres`, `shared-redis`,
  `shared-nginx`. `up-shared.sh` refuses to start without the network and warns
  if postgres/redis are down. Start it with `sudo /opt/shared/start-all.sh`.
* `/opt/Regulatory-Compliance-Agent/.env` exists, modelled on
  `.env.shared.example`, with at minimum:
  * `POSTGRES_PASSWORD` — must be the **same value** as `/opt/shared/.env`
  * `LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`
  * `AZURE_INFERENCE_ENDPOINT` / `AZURE_COHERE_EMBED_DEPLOYMENT` (the embedder;
    without them every retrieval call fails)
  * `RAG_EMBEDDING_MODEL` / `RAG_EMBEDDING_DIM` — **must match the model the
    stored corpus was embedded with.** `.env.shared.example` ships
    `embed-english-v3.0` while `docker-compose.shared.yml` defaults to
    `Cohere-embed-v3-multilingual`; whichever you pick, the corpus must agree.
    `verify_deploy.py` fails the deploy when they don't (see §5). The dimension
    is a one-way decision — see the trap below.
* Build inputs are clean in git. `up-shared.sh` aborts on uncommitted edits to
  the Dockerfiles / compose file / requirements.txt / package.json. Override
  with `ALLOW_DIRTY=1` only when you mean it.

---

## 2. Deploy

```bash
cd /opt/Regulatory-Compliance-Agent
git pull
sudo bash up-shared.sh
```

`up-shared.sh up` runs, in order:

1. `require_shared` — shared-network exists, shared infra is running
2. `guard_worktree` — the real Dockerfiles, not a scaffold's placeholder stubs
3. `drop_bundled` — removes `compliance-postgres` / `compliance-redis` (see §0)
4. `fix_mounts` — chowns `backend/uploads|logs|backups` to uid 10001 (`appuser`)
5. builds `localhost/compliance-backend:latest` + `localhost/compliance-frontend:latest`
6. smoke-tests both images before any container starts
7. force-removes and recreates the app containers, reloads shared-nginx
8. waits for the backend to serve on :8000
9. runs `python -m scripts.seed_rules` (skip with `NO_SEED=1`)
10. prints alembic revision + rule/precedent tag counts

Other subcommands: `build`, `verify`, `seed`, `ingest`, `down`, `ps`, `logs`, `tmux`.

**Migrations run themselves.** You never run alembic by hand on a normal deploy.
`backend/Dockerfile`'s CMD is
`sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"`,
and `docker-compose.shared.yml` overrides the entrypoint with the same thing
wrapped in a chown + `su appuser` (`alembic upgrade head && uvicorn ...`). So
every container start migrates to head first, and a failed migration means the
backend never listens — which is exactly why step 8 above is the health gate.

App: `http://<host>/compliance/` · API: `http://<host>/compliance/api/health`

### Trap: never run `alembic upgrade head` outside the backend container

The **vector column width is frozen at migration time**, and the fallback is
wrong for this deployment. `alembic/versions/0002_*.py` and `0012_precedent_cases.py`
both do:

```python
EMBED_DIM = int(os.getenv("RAG_EMBEDDING_DIM", "1536"))
...  embedding VECTOR({EMBED_DIM}) NOT NULL
```

Production embeds with `azure_cohere` / Cohere embed-v3 at **1024** dims. Run the
migrations from a host shell, a one-off migration job, or against a hand-made
database — anywhere `RAG_EMBEDDING_DIM` isn't set — and every vector column is
created as `vector(1536)`. The schema looks perfectly healthy; the first
embedding write then dies with `ERROR: expected 1536 dimensions, not 1024`, and
**re-running the migrations does not fix it** — the column type is already set
and needs an `ALTER`/rebuild.

The VM is safe today because migrations only ever run *inside* the backend
container, where `docker-compose.shared.yml` supplies
`RAG_EMBEDDING_DIM: ${RAG_EMBEDDING_DIM:-1024}`. Keep it that way: the supported
path is the container CMD, and `sudo bash up-shared.sh` never does anything else.
`verify_deploy.py`'s `vector_dim` check compares the real column width against
`settings.rag_embedding_dim` and fails loudly if they ever diverge.

---

## 3. One-off data backfill (reviewer workspace, migrations 0023–0029)

Run **once**, after the first deploy that carries these migrations, and after a
backup. Idempotent (every UPDATE is `WHERE <column> IS NULL`), so re-running is
harmless — but it is not part of `up-shared.sh` on purpose.

```bash
# always dry-run first — reports counts, writes nothing
sudo podman exec compliance-backend python -m scripts.backfill_reviewer_workspace --all --dry-run

# then for real
sudo podman exec compliance-backend python -m scripts.backfill_reviewer_workspace --all
```

Backfills `violations.analysis_run_id`, `violations.review_status/resolved_at`,
and `submissions.current_content`. What it deliberately does **not** touch, and
why, is documented in the script's own docstring — read it before assuming a
column should have been filled.

---

## 4. Admin provisioning

### How access control actually works

`backend/app/auth/permissions.py` holds a flat `ROLE_PERMISSIONS` dict; routes
declare `Depends(require("<scope>"))` (`backend/app/auth/dependencies.py`), which
403s and writes an `authz_denied` audit event when the caller's role lacks the
scope. There are **exactly three roles**, and `super_admin` is **not** a superset
of `admin`:

| scope | user | admin | super_admin |
|---|:--:|:--:|:--:|
| `submission:create` / `submission:read` / `submission:delete` | ✅ | ✅ | ❌ |
| `analysis:run`, `chat:use`, `comparison:use`, `dashboard:view` | ✅ | ✅ | ❌ |
| `knowledgebase:view`, `rules:read`, `feedback:submit` | ✅ | ✅ | ✅ |
| `rules:write`, `rules:generate` | ❌ | ✅ | ✅ |
| `users:manage` | ❌ | ✅ | ✅ |
| `feedback:review` (reviewer-action queues) | ❌ | ✅ | ✅ |
| `console:view`, `audit:view`, `usage:view` (`/super_admin/*`) | ❌ | ❌ | ✅ |

Read that table twice before picking a role:

* **`admin` is the role you want for day-to-day admin surfaces.** It is the only
  role that can both *use* the app (upload, analyse, review) and administer it
  (write rules, manage users, work the `feedback:review` queues).
* **`super_admin` cannot open a submission.** It has no `submission:read`,
  `dashboard:view` or `analysis:run`. It exists for the `/super_admin/*` console,
  audit log and usage/cost views. Provisioning only a super_admin and then
  wondering why the app looks empty is a predictable dead end.
* Today's deployed `grader1` is `role=user` (`scripts/seed_grader.py`) — it can
  never reach an admin route, by design.

**New admin-gated routers inherit this automatically.** A router added later
(e.g. corpus or retrieval admin surfaces under `/admin/*`) gates itself with
`Depends(require("<some scope>"))` against this same dict — in practice
`rules:write`, which `admin` and `super_admin` both hold. So the provisioning
recipe below does not need to know the route exists — provision the role, then
confirm reachability by hitting the route. If a brand-new scope is introduced,
it must also be added to `ROLE_PERMISSIONS` for the roles that should hold it,
or **every** caller gets a 403 no matter how they were provisioned. That is the
first thing to check when a new admin page 403s for a real admin.

### The one documented way to create/promote an admin

`backend/scripts/seed_admin.py`. Env-var driven, no password in any file,
idempotent, safe on every deploy.

```bash
sudo podman exec \
  -e ADMIN_USERNAME=compliance_admin \
  -e ADMIN_PASSWORD='<from the secret store, >=12 chars>' \
  compliance-backend python -m scripts.seed_admin
```

* user missing → created with `role=admin`, `must_change_password=true`,
  `registered_ip=0.0.0.0` (wildcard — any device)
* user present → **password is never touched**; only the role is set. Re-running
  on every deploy cannot clobber a password the admin has since changed.
* deactivated account → reported, **not** silently reactivated
* exits `2` on misconfiguration (no username, password under 12 chars,
  unknown `ADMIN_ROLE`)

`ADMIN_ROLE` defaults to `admin`; set `ADMIN_ROLE=super_admin` for a console
account. It is the source of truth, so it can also **demote** — don't point it
at an existing account casually.

Set `ADMIN_IP=<client ip>` instead of the wildcard only if you also set
`AUTH_IP_BINDING_MODE=strict`. This VM runs `log_only` (see
`docker-compose.shared.yml`), where the IP is recorded but not enforced.

Related, and intentionally left alone: `scripts/seed_super_admin.py` still exists
for the original `SUPER_ADMIN_USERNAME`/`SUPER_ADMIN_PASSWORD` settings path, but
it only ever creates a `super_admin` and no-ops once the username exists — it
cannot promote, and cannot make an `admin`. Use `seed_admin.py`.

### Confirm the gating actually works

First login forces a password change (`must_change_password`), so do that in the
browser at `http://<host>/compliance/`, then:

```bash
# as the admin — expect 200
curl -s -c /tmp/a.txt -X POST http://<host>/compliance/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"compliance_admin","password":"<new password>"}'
curl -s -b /tmp/a.txt http://<host>/compliance/api/auth/me          # role: admin
curl -s -o /dev/null -w '%{http_code}\n' -b /tmp/a.txt \
  http://<host>/compliance/api/compliance/reviewer-actions/queues   # 200 (feedback:review)

# as grader1 (role=user) — expect 403 on the same route
curl -s -c /tmp/g.txt -X POST http://<host>/compliance/api/auth/login \
  -H 'Content-Type: application/json' -d '{"username":"grader1","password":"<pw>"}'
curl -s -o /dev/null -w '%{http_code}\n' -b /tmp/g.txt \
  http://<host>/compliance/api/compliance/reviewer-actions/queues   # 403
```

A 403 for the non-admin is the check passing, not failing — it also lands an
`authz_denied` row in `audit_events`. Substitute any admin-gated path for the
queues URL; the negative test is the one that proves the gate exists.

---

## 5. Verify the deploy

```bash
sudo podman exec compliance-backend python -m scripts.verify_deploy
```

Read-only — it writes nothing. Exits `1` if anything FAILs (WARN still exits `0`).
Run it **inside the container**: `gotenberg` and `shared-postgres` are
compose-network DNS names that do not resolve from the host.

```
CHECK       STATUS  DETAIL
alembic     PASS    at head ['0030']
tables      PASS    all 28 expected tables present
rows        PASS    rules=65, precedent_cases=1, submissions=0
admins      PASS    admin=1
vector_dim  PASS    all 7 vector column(s) are vector(1024)
embeddings  PASS    single-model Cohere-embed-v3-multilingual across 1 table(s)
gotenberg   PASS    http://gotenberg:3000/health -> HTTP 200
```

| Check | FAILs when |
|---|---|
| `alembic` | the DB is behind (or ahead of) the head shipped in the image |
| `tables` | any table declared by the models or the raw-SQL RAG migrations is missing |
| `rows` | `rules` is empty (seed never ran). Empty `precedent_cases` is a WARN — retrieval degraded, app still serves |
| `admins` | no active user holds `users:manage`. Roles are derived from `permissions.py`, so a new admin-ish role counts automatically |
| `vector_dim` | a vector column's width ≠ `RAG_EMBEDDING_DIM` — migrations ran without the env var (see §2's trap). Not fixable by re-migrating |
| `embeddings` | a vector table holds more than one `embedding_model`, or the stored model isn't the configured `RAG_EMBEDDING_MODEL`. Same idea as `db_restore.sh`'s provenance report, but fail-closed — this mirrors `pgvector_store._assert_embedding_compat`, which refuses to serve a cross-model corpus at query time |
| `gotenberg` | the DOCX→PDF sidecar doesn't answer — submission export is broken |

Two related sanity commands: `sudo bash up-shared.sh verify` (checks the running
container's contents and prints alembic + tag counts) and `sudo bash up-shared.sh ps`.

---

## 6. Rollback

Take a dump **before** anything destructive:

```bash
sudo podman exec compliance-backup /scripts/db_backup.sh   # -> backend/backups/
```

**Code-only rollback** (no migration in the bad release) — rebuild the previous
commit; the containers migrate to that commit's head on start:

```bash
cd /opt/Regulatory-Compliance-Agent
git log --oneline -5
git checkout <good-sha>
sudo bash up-shared.sh
sudo podman exec compliance-backend python -m scripts.verify_deploy
```

**Rollback across a migration.** `alembic upgrade head` runs on every container
start, so rolling the code back alone leaves the schema ahead of it. Downgrade
first, while the new image is still running, then deploy the old commit:

```bash
sudo podman exec compliance-backend alembic downgrade <target-revision>
git checkout <good-sha>
sudo bash up-shared.sh
```

Every migration in `backend/alembic/versions/` implements `downgrade()`, but a
downgrade that drops a column **drops its data**. Take the backup first, and
prefer rolling forward with a fix.

**Full data restore** (last resort — replaces the database contents):

```bash
sudo podman exec compliance-backup sh /scripts/db_restore.sh /backups/compliance_db_<ts>.sql.gz
```

`db_restore.sh` refuses dumps under 10 KB or with a broken gzip, and prints the
embedding provenance of the restored corpus. If it reports more than one
`(model, dim)` pair, `verify_deploy.py`'s `embeddings` check will fail too — fix
it with a single-model re-ingest, don't ignore it.

**Stopping the app** leaves the shared infra alone:

```bash
sudo bash up-shared.sh down     # == podman-compose -f docker-compose.shared.yml down
```
