---
type: Reference
title: Deployment Topology
description: The docker-compose stack (postgres+pgvector, redis, backend, frontend, backup sidecar), corporate CA/TLS handling, migrations on boot, and the internal-UAT hosting model.
resource: docker-compose.yml
tags: [config, deployment, docker, infra]
timestamp: 2026-07-03T12:00:00Z
---

# Deployment Topology

Hosted on an **internal UAT VM** reachable only from the corporate Wi-Fi (not exposed to the public internet).

## Compose stack (`docker-compose.yml`)

| Service | Image / build | Role |
|---------|---------------|------|
| `postgres` | `pgvector/pgvector:pg15` | database + vector store (port 5432) |
| `redis` | `redis:7-alpine` | LangGraph checkpointing, rate-limit, budget (port 6379) |
| `backend` | `./backend` | FastAPI (port 8000); `mem_limit 2g`, `cpus 2.0` |
| `frontend` | `./frontend` | Next.js (port 3000) |
| `db-backup` | `postgres:15-alpine` | daily `pg_dump`, 14-day retention |

Variants: `docker-compose.prod.yml`, `.shared.yml`, `.override.yml`; deploy scripts under `scripts/deploy/`; a shared nginx
front (`shared/nginx.conf`).

## Boot & migrations

The backend Dockerfile runs `alembic upgrade head` before uvicorn, so the schema migrates on every boot. Seed once:
`python -m scripts.seed_rules` and `python -m scripts.ingest_knowledge_base`.

## Corporate CA / TLS

The backend Dockerfile registers Bajaj / Cisco-Umbrella root CAs (`backend/certs/`) and pre-caches the tiktoken BPE, so it works
behind the corporate SSL-inspection proxy with no public-CDN calls. `LLM_INSECURE_TLS` bypasses verify for external HTTPS
providers behind SSL inspection.

## Frontend base URLs

Build-time `NEXT_PUBLIC_API_BASE` + `INTERNAL_API_BASE` (docker DNS `http://backend:8000`); `NEXT_PUBLIC_BASE_PATH` supports
sub-path deploys.

## Related

- Settings in [LLM & RAG config](llm-and-rag.md). A proposed auth/monitoring layer is designed in the repo's `audit-trail/`.
