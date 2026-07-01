# Shared Infrastructure (`/opt/shared`)

Single set of shared services for **every** app on this VM (RHEL9, rootful
Podman 5.8.2, podman-compose 1.6.0):

| Container | Image | Role |
|-------------------|----------------------------------------|----------------------------------------|
| `shared-postgres` | `docker.io/pgvector/pgvector:pg15` | One Postgres (pgvector) for all apps |
| `shared-redis` | `docker.io/library/redis:7-alpine` | One Redis for all apps |
| `shared-nginx` | `docker.io/library/nginx:alpine` | Reverse proxy — the only public port 80 |

Network: **`shared-network`** (bridge). Apps join it as an *external* network.
Volume: **`pgdata`** (named) — Postgres data survives app re-deploys.

Each app keeps its own compose file in its own directory (e.g.
`/opt/Regulatory-Compliance-Agent`). Apps no longer publish ports — nginx is the
single entry point.

---

## Architecture

```
 host :80
 │
 ┌───────────────┐
 browser ───────► │ shared-nginx │
 └───────┬───────┘
 /compliance/ │ /compliance/api/
 ▼ │ ▼
 compliance-frontend:3000 compliance-backend:8000
 │
 ┌───────────────┴───────────────┐
 ▼ ▼
 shared-postgres:5432 shared-redis:6379
 (db: compliance, all on shared-network
 pgvector)
```

Everything (apps + infra) is attached to `shared-network`, so containers reach
each other by **container name** (`shared-postgres`, `compliance-backend`, …).

---

## Start order (important)

`depends_on` only works **within a single compose file** — an app cannot
`depends_on: shared-postgres` because that service lives in a different project.
Ordering is therefore handled by `start-all.sh`, not by compose:

```bash
sudo /opt/shared/start-all.sh
```

It (1) brings up shared infra, (2) waits for `shared-postgres` to be healthy,
(3) starts each registered app, then (4) restarts `shared-nginx` so it resolves
the now-running app upstreams. nginx also has `restart: always`, so it self-heals
if it boots before the app containers exist.

Manual equivalent:

```bash
cd /opt/shared && sudo podman-compose up -d
# wait until: sudo podman inspect -f '{{.State.Health.Status}}' shared-postgres => healthy
cd /opt/Regulatory-Compliance-Agent && sudo podman-compose up -d
sudo podman restart shared-nginx
```

---

## Nginx routing table

| Public path | Proxied to (container:port) | Notes |
|-------------------------|-------------------------------|--------------------------------|
| `/health` | nginx itself | returns `200 OK` |
| `/compliance/` | `compliance-frontend:3000` | Next.js UI |
| `/compliance/api/` | `compliance-backend:8000` | FastAPI (prefix stripped) |
| `/app2/` *(template)* | `app2-frontend:3002` | commented out in `nginx.conf` |
| `/app2/api/` *(template)* | `app2-backend:8001` | commented out in `nginx.conf` |

> The `/compliance/api/` block is declared **before** `/compliance/` so the API
> prefix isn't swallowed by the frontend route. Keep that ordering for every app.

**Caveat (subpath):** the compliance frontend is built to serve at root `/`.
Serving it under `/compliance/` returns HTML correctly, but Next.js asset paths
(`/_next/...`) assume root. For a clean subpath deployment the frontend image
should be rebuilt with `basePath: '/compliance'` (and the API base set to
`/compliance/api`). That requires a frontend rebuild and is out of scope of this
infra change (Dockerfiles are not modified here).

### Reload nginx without downtime

After editing `nginx.conf`:

```bash
sudo podman exec shared-nginx nginx -t # validate first
sudo podman exec shared-nginx nginx -s reload # hot reload, no dropped connections
```

---

## Adding a new app

1. **Pick ports** from the registry below (no clashes).
2. **Create the app's database** in shared Postgres (one-time):
 ```bash
 sudo podman exec -it shared-postgres psql -U postgres -c "CREATE DATABASE app2;"
 sudo podman exec -it shared-postgres psql -U postgres -d app2 -c "CREATE EXTENSION IF NOT EXISTS vector;"
 ```
3. **Write the app compose** (`/opt/<app>/docker-compose.yml`):
 - container names `<app>-frontend`, `<app>-backend`;
 - **no** host port mappings;
 - attach every service to the external `shared-network`:
 ```yaml
 networks:
 shared-network:
 external: true
 name: shared-network
 ```
 - point `DATABASE_URL`/`REDIS_URL` at `shared-postgres` / `shared-redis`.
4. **Add a route** in `/opt/shared/nginx.conf` (copy the App 2 template, set the
 prefix + upstreams), then `nginx -t` and `nginx -s reload`.
5. **Register it** in `start-all.sh` → append to the `APPS=( ... )` array.

### Naming conventions

- Containers: `<appname>-frontend`, `<appname>-backend` (hyphens).
- Nginx upstreams/targets: `<appname>_frontend`, `<appname>_backend`
 (**underscores** — nginx names can't contain hyphens).

### Port allocation registry

Internal container ports (not published to the host — nginx fronts everything).
Increment frontend by **2**, backend by **1** for each new app.

| App | Frontend (internal) | Backend (internal) |
|--------------------|---------------------|--------------------|
| App 1 — Compliance | 3000 | 8000 |
| App 2 | 3002 | 8001 |
| App 3 | 3004 | 8002 |

---

## Verify

```bash
curl http://localhost/health # OK
curl http://localhost/compliance/ # frontend HTML
curl http://localhost/compliance/api/docs # FastAPI Swagger UI
```
