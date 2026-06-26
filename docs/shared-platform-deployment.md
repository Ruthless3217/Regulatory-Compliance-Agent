# Deploying on the Bajaj AI Platform (shared stack)

This agent can run two ways:

| Mode             | Compose file                 | Postgres/Redis/Nginx        |
|------------------|------------------------------|-----------------------------|
| **Standalone** (local dev) | `docker-compose.yml` (+ override) | bundled in this repo   |
| **Shared platform** (VM / production) | `docker-compose.shared.yml` | the platform `shared/` stack |

This doc covers the **shared platform** mode — the production path. The
[bajaj-ai-platform](https://github.com/Thunderk3g/bajaj-ai-infra) repo owns a
standalone `shared/` stack (`shared-postgres` / `shared-redis` / `shared-nginx`)
that is brought up **separately, once**, and every agent joins it. You bring up
the infra first, then this agent — they are independent compose stacks.

> **This agent is already registered in the platform** — no platform edits
> needed on a fresh clone:
> - `shared/init-db.sh` seeds the `compliance_user` role + `compliance_db` (pgvector enabled).
> - `shared/nginx.conf` ships the `/compliance/` and `/compliance/api/` routes.
> - `docs/port-registry.md` lists `compliance-frontend:3000` / `compliance-backend:8000` as **active**.
>
> So the whole job is: (1) bring up `shared/` separately, (2) `up -d` this agent
> against the external `shared-network`.

## What makes it platform-ready

- `docker-compose.shared.yml` — only `compliance-frontend` + `compliance-backend`,
  no `ports:`, joins the **external** `shared-network`.
- Frontend builds with `NEXT_PUBLIC_BASE_PATH=/compliance` (sub-path assets),
  `NEXT_PUBLIC_API_BASE=/compliance/api` (browser → nginx → backend), and
  `INTERNAL_API_BASE=http://compliance-backend:8000` (SSR → backend by name).
- Backend connects to `shared-postgres` / `shared-redis` and uses the dedicated
  `compliance_user` role seeded by `shared/init-db.sh`.

## Prerequisites on the VM

1. The shared stack is up:

   ```bash
   cd /opt/shared
   sudo podman-compose up -d
   curl http://localhost/health        # OK
   ```

2. `shared/init-db.sh` has seeded `compliance_db` + the `compliance_user` role
   (it runs automatically on first postgres start). `COMPLIANCE_DB_PASSWORD` in
   `/opt/shared/.env` is the password for that role.

   > If postgres was already initialized before the role existed, create it once:
   > ```bash
   > sudo podman exec -it shared-postgres psql -U postgres -c \
   >   "CREATE ROLE compliance_user LOGIN PASSWORD 'compliance_pass';"
   > sudo podman exec -it shared-postgres psql -U postgres -c \
   >   "GRANT ALL PRIVILEGES ON DATABASE compliance_db TO compliance_user;"
   > sudo podman exec -it shared-postgres psql -U postgres -d compliance_db -c \
   >   "GRANT ALL ON SCHEMA public TO compliance_user;"
   > ```

## Quick boot — `up-shared.sh`

Once the shared stack is up (above), the whole agent is one command:

```bash
sudo ./up-shared.sh           # cleans stray bundled containers, builds, starts on shared infra, reloads nginx
sudo ./up-shared.sh ps        # show the running stack
sudo ./up-shared.sh logs      # tail backend logs
sudo ./up-shared.sh ingest    # re-embed the knowledge base
sudo ./up-shared.sh down      # stop the agent (shared infra left running)
```

It runs **only** `compliance-backend` + `compliance-frontend` against the shared
stack and removes any leftover bundled `compliance-postgres` / `compliance-redis`
/ `compliance-backup` from a prior standalone run, so you can't accidentally talk
to the wrong database. (Local engine? `ENGINE=docker ./up-shared.sh`.)

The manual equivalent is below.

## Deploy (manual)

```bash
# Clone the agent next to the platform dirs (any path is fine; build context
# is relative). Example:
cd /opt
sudo git clone https://github.com/Ruthless3217/Regulatory-Compliance-Agent.git compliance-agent
cd /opt/compliance-agent

# The /dataset/ folder is git-ignored (large embedded corpus). Copy it onto the
# VM separately, or KB ingestion / rule seeding will have nothing to load:
#   scp -r dataset/ user@vm:/opt/compliance-agent/dataset/

sudo cp .env.shared.example .env
sudo nano .env            # set: COMPLIANCE_DB_PASSWORD (MUST match shared/.env),
                          #      LLM_API_KEY (Azure Foundry key — same key serves the
                          #      LLM and the Cohere embed/rerank inference surface),
                          #      AZURE_COHERE_EMBED_DEPLOYMENT, etc.

sudo podman-compose -f docker-compose.shared.yml up -d --build

# Add nginx routes if not already present, then reload:
cd /opt/shared
sudo ./scripts/add-agent-route.sh compliance 3000 8000   # idempotent-ish; skip if already in nginx.conf
sudo podman exec shared-nginx nginx -s reload
```

> The platform's `nginx.conf` already ships the `/compliance/` and
> `/compliance/api/` routes, so on a fresh platform clone you can skip
> `add-agent-route.sh` and just reload nginx.

## First-run data (rules + knowledge base)

Alembic migrations run automatically on backend start. Seed the rules/KB once:

```bash
# Example — adjust to your scripts:
sudo podman exec -it compliance-backend python -m dataset.seed   # or psql -f seed_extracted_rules.sql
```

> **Re-ingest after the embedding switch.** The knowledge base must be embedded
> with the **same** model used at query time. The production embedder is now
> `Cohere-embed-v3-multilingual` (1024-dim) via Azure AI Foundry — if the corpus
> was previously embedded with public `embed-english-v3.0`, re-ingest so the
> stored vectors match (dim is unchanged, so no DB migration — only re-embedding):
> ```bash
> sudo podman exec -it compliance-backend python -m scripts.ingest_knowledge_base
> ```

## Verify

```bash
curl http://10.3.5.99/compliance/             # frontend HTML (assets under /compliance/_next/)
curl http://10.3.5.99/compliance/api/health   # {"status":"healthy",...}
curl http://10.3.5.99/compliance/api/docs     # FastAPI docs
```

## Updating

```bash
cd /opt/compliance-agent
sudo git pull
sudo podman-compose -f docker-compose.shared.yml up -d --build
sudo podman exec shared-nginx nginx -s reload
```
