# Regulatory Compliance Agent — VM Technical Setup & Access (RHEL 9)

**Application:** Regulatory Compliance Agent
**Owner team:** AI / Marketing (Bajaj Life Insurance)
**Scope of this document:** technical requirements only — network whitelist, host ports, privileged setup, CA trust, and deployment artifacts. No business justification.

**Deployment model:** Single RHEL 9 VM running the full stack via Docker Engine + Compose. Container images, Python (PyPI), Node (npm) and OS (dnf) packages are pulled from the **internal Bajaj Artifactory / registry mirror**. The only external egress is the LLM/embedding endpoint (cannot be mirrored).

**Server login IDs in scope:** diwakar.adhikari01, yashraj.lawate, abhishek.gurjar, tanish.jagtap, utkarsh.das

> **Dev override must be excluded on the VM.** The repo ships `docker-compose.override.yml` (dev-only: remaps frontend to 3001, sets `LLM_INSECURE_TLS=true` to skip TLS verification). Docker Compose auto-merges it. **Rename or delete it before `docker compose up`** so the VM runs the base `docker-compose.yml` only: frontend **3000**, backend **8000**, full TLS validation against the corporate CA chain (§3).

---

## 1. Stack & runtime topology

Five containers on one VM (defined in `docker-compose.yml`):

| Container | Image | Host port | Reachable from |
|---|---|---|---|
| `compliance-frontend` | built from `./frontend` (Next.js 15 / React 19) | **3000/tcp** | Users (internal network) |
| `compliance-backend` | built from `./backend` (FastAPI + LangGraph, Python 3.11) | **8000/tcp** | Users + frontend |
| `compliance-postgres` | `pgvector/pgvector:pg15` | 5432 (internal) | backend only |
| `compliance-redis` | `redis:7-alpine` | 6379 (internal) | backend only |
| `compliance-backup` | `postgres:15-alpine` | — | internal (daily DB dump sidecar, 14-day retention) |

**Inbound host firewall (internal network only):** open **3000/tcp** and **8000/tcp**. Keep 5432 and 6379 **container-internal** — do not expose on the host.

**Data egress:** no customer data or PII. Only the pre-publication marketing copy under review is sent (chunked) to the single Azure endpoint in §2a — a Bajaj-tenant Azure resource. All reviewer data, scores, reports, DB and knowledge base stay on the VM.

---

## 2. Internet access — URLs to whitelist

Images and language packages come from the **internal Artifactory / registry mirror**, so the external whitelist is minimal.

### 2a. Required — runtime, continuous (whole stack lifetime)

Production uses a **single external endpoint** — a Bajaj-tenant **Azure OpenAI / AI Foundry** resource. It is the *only* LLM provider, and (with an embedding deployment on the same resource) also serves the embeddings.

| # | URL / Host | Port | Protocol | Use case | Duration |
|---|------------|------|----------|----------|----------|
| 1 | `bl-prod02-opai-bajaj-compliance-app-01.openai.azure.com` | 443 | HTTPS | Bajaj Azure OpenAI / AI Foundry resource. Serves the LLM (compliance analysis, chat, rule extraction) and the `text-embedding-3-small` embeddings for precedent retrieval (RAG). App is non-functional without it. | Permanent (life of app) |

> Production LLM Target URI (Azure portal): `https://bl-prod02-opai-bajaj-compliance-app-01.openai.azure.com/openai/responses?api-version=2025-04-01-preview`. The firewall rule only needs the **host** above on port 443. The app is wired against the same resource's OpenAI-compatible `/openai/v1/` surface (see §5).
>
> This host is intercepted by Cisco Umbrella SSL inspection. The app trusts the Bajaj root + Cisco Umbrella CA chain (registered into the VM trust store in §3a), so no TLS-bypass is required. As a Bajaj-tenant Azure resource, the marketing copy stays within Bajaj's Azure subscription, not a public third-party AI API.

### 2b. Build / setup — served from internal mirror (confirm internal hostnames with Infra)

Normally **internal Artifactory** proxies, not public internet. Public origin shown only as fallback.

| Purpose | Internal mirror (to confirm) | Public origin (only if no mirror) | Port |
|---------|------------------------------|-----------------------------------|------|
| Container images (pgvector/pg15, redis:7, postgres:15, python:3.11-slim, node base) | Internal Docker registry mirror | `registry-1.docker.io`, `auth.docker.io`, `production.cloudflare.docker.com` | 443 |
| Docker Engine RPMs | Internal dnf/Docker-CE repo mirror | `download.docker.com` | 443 |
| RHEL OS packages | Internal RHEL/Satellite repo | Red Hat CDN | 443 |
| Python packages (pip) | Internal PyPI index | `pypi.org`, `files.pythonhosted.org` | 443 |
| Node packages (npm) | Internal npm registry | `registry.npmjs.org` | 443 |
| Debian base-image apt (build-essential, libpq-dev, poppler-utils) | Internal Debian mirror | `deb.debian.org`, `security.debian.org` | 80/443 |

> **Build-time egress is eliminated if you ship pre-built images.** Build the images where mirror/internet access exists, push to the internal Docker registry, and on the VM replace the compose `build:` blocks with `image:` references. The VM then only needs the §2a runtime host. Note: the frontend bakes its API base URL at build time (`NEXT_PUBLIC_API_BASE` / `INTERNAL_API_BASE` build args), and build for `linux/amd64`.

### 2c. Conditional — OFF by default, keep blocked unless the feature is enabled

| URL / Host | Port | When needed |
|------------|------|-------------|
| `openaipublic.blob.core.windows.net` | 443 | tiktoken tokenizer download. **Pre-cached in the image — not needed at runtime.** Keep blocked. |
| `api.smith.langchain.com` | 443 | LangSmith tracing. Disabled by default (`LANGCHAIN_TRACING_V2=false`). |
| `generativelanguage.googleapis.com`, `api.groq.com`, `api.openai.com` | 443 | **Dev/local-only** LLM/embedding providers. **Not used in production** (production uses the single Azure host in §2a). Keep blocked on the VM. |
| `api.cohere.com` | 443 | Only if embeddings switched to Cohere. Not used in production. |
| `<search-service>.search.windows.net`, Pinecone endpoints | 443 | Only if the vector store is switched away from on-VM pgvector. Not used by default (pgvector runs on the VM, no egress). |

---

## 3. Privileged (sudo) access — host setup

All commands are one-time **setup/maintenance** of a Docker host. Day-to-day app operation runs as the non-root `docker` group user and needs no sudo.

**(i) Install Docker Engine + Compose plugin** (from internal mirror repo)
```bash
sudo dnf install -y dnf-plugins-core
sudo dnf config-manager --add-repo <INTERNAL_ARTIFACTORY>/docker-ce/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io \
                    docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

**(ii) Allow the project team to run containers without per-command root**
```bash
sudo usermod -aG docker diwakar.adhikari01
sudo usermod -aG docker yashraj.lawate
sudo usermod -aG docker abhishek.gurjar
sudo usermod -aG docker tanish.jagtap
sudo usermod -aG docker utkarsh.das
```

**(iii) Point Docker at the internal registry mirror + corporate proxy**
```bash
# /etc/docker/daemon.json -> registry-mirrors / insecure-registries (internal)
sudo install -d /etc/docker
sudo tee /etc/docker/daemon.json   # write registry-mirror config
# Proxy for the daemon to reach internal Artifactory / Azure OpenAI:
sudo install -d /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/http-proxy.conf   # HTTP(S)_PROXY
sudo systemctl daemon-reload
sudo systemctl restart docker
```

**(iv) Register Bajaj + Cisco Umbrella root CAs into the VM trust store** (so SSL-inspected HTTPS to the Azure OpenAI host validates)
```bash
sudo cp certs/bajaj-root.pem certs/cisco-umbrella-root.pem \
        certs/cisco-umbrella-primary.pem certs/cisco-umbrella-secondary.pem \
        /etc/pki/ca-trust/source/anchors/
sudo update-ca-trust
```

**(v) Open the application ports in firewalld**
```bash
sudo firewall-cmd --permanent --add-port=3000/tcp   # frontend (Next.js)
sudo firewall-cmd --permanent --add-port=8000/tcp   # backend API (FastAPI)
sudo firewall-cmd --reload
```

**(vi) SELinux labelling for bind-mounted data directories**
```bash
sudo chcon -Rt container_file_t /opt/regulatory-compliance/{uploads,logs,backups,dataset,docs}
# persistent: semanage fcontext -a -t container_file_t '<path>(/.*)?' && restorecon -Rv <path>
```

**(vii) Create the application directory and hand ownership to the team**
```bash
sudo mkdir -p /opt/regulatory-compliance
sudo chown -R diwakar.adhikari01:appteam /opt/regulatory-compliance
sudo dnf install -y git    # if pulling code from internal GitLab/Bitbucket
```

**(viii) Service lifecycle during maintenance / patching**
```bash
sudo systemctl restart docker
sudo systemctl status docker
sudo dnf update -y docker-ce docker-ce-cli containerd.io
```

> Scoped sudoers rather than `ALL=(ALL) ALL`: limit to `dnf`, `systemctl` (docker/containerd), `usermod -aG docker`, `firewall-cmd`, `update-ca-trust`, `cp`/`tee` into the CA-anchor and docker config paths, `chcon`/`semanage`/`restorecon`, and `mkdir`/`chown` under `/opt/regulatory-compliance`.
>
> `docker` group membership is effectively root-equivalent (daemon runs as root). VM is single-tenant (this app only). Rootless Docker / Podman is the alternative if Security prefers to avoid `docker` group.

---

## 4. PAM file transfer — deployment artifacts

Non-interactive artifacts moved onto/off the VM via ARCON PAM (auditable, session-recorded). No bulk or ad-hoc export.

**Upload (into the VM):**
- Application source / deployment bundle (Compose files + backend/frontend code), if not pulled via internal git.
- Corporate root-CA bundle (`bajaj-root.pem`, `cisco-umbrella-*.pem`) for the trust store.
- Environment config (`.env`) containing the **Azure OpenAI API key** and DB credentials — sensitive, via PAM not email. (`.env` is gitignored; never committed.)
- Reviewer-decision dataset and guideline docs to seed the knowledge base (read-only reference corpus).

**Download (off the VM):**
- Application/diagnostic logs and Postgres backup dumps (daily, 14-day retention) for troubleshooting and DR validation.

---

## 5. Application configuration (`.env` on the VM)

**LLM (Azure — production):** the backend builds a generic `AsyncOpenAI(base_url=...)` client calling **Chat Completions**, so point it at the resource's OpenAI-compatible `/openai/v1/` surface — **not** the `/openai/responses?api-version=...` Target URI.

```bash
LLM_BASE_URL=https://bl-prod02-opai-bajaj-compliance-app-01.openai.azure.com/openai/v1/
LLM_MODEL=<chat-deployment-name>          # from the resource's Deployments tab (e.g. gpt-4o) — not the URI path
LLM_API_KEY=<AZURE_OPENAI_KEY>            # PAM-transferred into .env only; never committed
LLM_INSECURE_TLS=false                     # TLS verification ON in production
```

> Using the `/openai/responses` or classic `/openai/deployments/<dep>/...?api-version=...` URL forms instead would require switching `_build_client` in `app/services/llm_service.py` to `AsyncAzureOpenAI` (and, for Responses, a larger rewrite). Only do that if the resource does not expose `/openai/v1/`.

**Embeddings (RAG):** still required; deploy an embedding model on the **same** Azure resource to keep a single egress host.
```bash
RAG_EMBEDDING_PROVIDER=azure_openai
AZURE_OPENAI_ENDPOINT=https://bl-prod02-opai-bajaj-compliance-app-01.openai.azure.com
AZURE_OPENAI_API_KEY=<AZURE_OPENAI_KEY>
AZURE_OPENAI_EMBED_DEPLOYMENT=<embedding-deployment-name>   # e.g. text-embedding-3-small
AZURE_OPENAI_API_VERSION=2024-02-01
RAG_EMBEDDING_DIM=1536          # must match the ingested corpus
```

> Confirm an embedding deployment exists on the resource — the production Target URI is a chat/responses deployment only. If you change embedding model/dim, re-ingest the knowledge base so query and stored vectors stay comparable.

**Other:** `DATABASE_URL` and `REDIS_URL` are container-internal (set in compose). Vector store defaults to on-VM pgvector (no egress).

---

## 6. First-run steps (after `docker compose up -d`)

The backend runs `alembic upgrade head` on boot (schema migration). Then once:
```bash
# Seed starter rules (IRDAI / Bajaj brand / SEBI)
docker exec compliance-backend python -m scripts.seed_rules

# Ingest the reviewer-decision corpus that powers grading (calls the embeddings API)
docker exec compliance-backend python -m scripts.ingest_knowledge_base
```
Compose mounts `./dataset` and `./docs` read-only into the backend; the backup sidecar dumps Postgres daily.

---

## 7. Quick checklist

- [ ] Open inbound **3000/tcp** and **8000/tcp** (internal network); keep 5432 / 6379 container-internal.
- [ ] Whitelist outbound **443** to `bl-prod02-opai-bajaj-compliance-app-01.openai.azure.com` (only external egress).
- [ ] Register Bajaj + Cisco Umbrella root CAs into the VM trust store.
- [ ] `.env` (with Azure key) delivered via PAM, not committed; `LLM_BASE_URL` set to the `/openai/v1/` surface.
- [ ] Confirm chat **and** embedding deployment names on the Azure resource.
- [ ] **Delete/rename `docker-compose.override.yml`** before starting on the VM.
- [ ] Prefer pre-built images from the internal registry to drop all §2b build hosts.
