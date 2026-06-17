# VM Deployment & Network Whitelisting Guide

**Application:** Regulatory Compliance Agent
**Owner:** AI / Marketing (Bajaj Life Insurance)
**Purpose of this doc:** what to whitelist to install and run the app on a VM, whether consuming a pre-built Docker image is simpler, and a short description of what the app does.

---

## 1. What this application does

The Regulatory Compliance Agent is an internal AI tool that reviews Bajaj Life Insurance **marketing copy** (ads, brochures, social posts, web pages) against **IRDAI / SEBI regulations and Bajaj brand guidelines — before publication**. A reviewer pastes or uploads a draft and gets back inline-highlighted violations, a compliance score and letter grade, a shareable report, and a chat interface to ask follow-up questions or request compliant rewrites. The goal is to turn what is today a slow, manual, reviewer-dependent process into a minutes-long, consistent, auditable check.

What makes it different from a plain rules checker is that it **grades by imitating real past human-reviewer decisions ("precedents")**, not just static rules. Those past decisions are stored as vector embeddings in a knowledge base (PostgreSQL + pgvector). When a new draft arrives, the app chunks it, embeds each passage, retrieves the most similar past reviewer decisions plus the relevant rules, and asks a large language model (LLM) to grade the new copy the way reviewers historically did. The orchestration is a FastAPI + LangGraph backend; the UI is a Next.js / React frontend; Redis holds the LangGraph run state. **No customer data or PII is processed** — the only content that leaves the VM is the marketing draft text itself (chunked), sent to the external LLM and embedding APIs listed in §3.

**Stack at a glance:** Next.js 15 / React 19 frontend · FastAPI + LangGraph backend (Python 3.11) · PostgreSQL 15 + pgvector · Redis 7 · pluggable LLM (Gemini / Groq / OpenAI-compatible) and embeddings (OpenAI / Azure OpenAI / Cohere).

> **Production provider: Azure Microsoft AI Foundry.** In production both the LLM and the embeddings are served from an Azure AI Foundry / Azure OpenAI resource. This changes the egress whitelist (§3) and requires the specific config in **§3d** — read that section before deploying to production.

---

## 2. Deployment shape (what runs where)

The whole stack runs on a single VM via **Docker Engine + Docker Compose**. Five containers, all defined in `docker-compose.yml`:

| Container | Image | Port (host) | Reachable from |
|---|---|---|---|
| `compliance-frontend` | built from `./frontend` | **3000** | Users (internal network) |
| `compliance-backend` | built from `./backend` | **8000** | Users + frontend |
| `compliance-postgres` | `pgvector/pgvector:pg15` | 5432 | backend only (internal) |
| `compliance-redis` | `redis:7-alpine` | 6379 | backend only (internal) |
| `compliance-backup` | `postgres:15-alpine` | — | internal (daily DB dump sidecar) |

> ⚠️ **Exclude the dev override on the VM.** The repo ships `docker-compose.override.yml`, which is **local-dev only** — it remaps the frontend to port 3001, switches the LLM to Gemini, and sets `LLM_INSECURE_TLS=true` (skips TLS verification). Docker Compose auto-merges it. **Rename or delete it before `docker compose up` on the VM** so you run the base `docker-compose.yml` only (frontend 3000, backend 8000, full TLS validation).

### Inbound — ports to open *inside the corporate network* (not the internet)
These are for users to reach the app; they are **not** internet-facing whitelist entries.

| Port | Service | Who needs it |
|---|---|---|
| **3000/tcp** | Frontend UI | End users (reviewers) |
| **8000/tcp** | Backend API + `/docs` (Swagger) | End users' browsers (the frontend calls the API), and anyone using the API directly |

5432 (Postgres) and 6379 (Redis) stay **container-internal** — do not expose them on the host firewall.

---

## 3. Outbound internet egress — URLs to whitelist

This is the part that actually needs a firewall/proxy exception. Egress is intentionally minimal. All are **HTTPS on port 443**.

> **Note on the proxy chain:** Bajaj's Cisco Umbrella SIG performs TLS inspection on outbound HTTPS. The backend image bakes in the corporate root CAs (`backend/certs/*.pem`, registered in the Dockerfile) so Python clients don't fail with `CERTIFICATE_VERIFY_FAILED`. If those certs rotate, the image must be rebuilt.

### 3a. Required at runtime — pick based on your configured providers

The LLM and embedding providers are **driven by environment variables**, so whitelist the hosts that match your `.env`.

> **For production, skip to §3d (Azure AI Foundry)** — that is the production provider, and its host replaces the Gemini/OpenAI hosts below. The table below applies to dev / non-Azure setups.

| Host | Port | Why | When required |
|---|---|---|---|
| **`generativelanguage.googleapis.com`** | 443 | Google **Gemini** — the LLM that grades copy, answers chat, extracts rules | If `LLM_BASE_URL` points to Gemini (current team default) |
| **`api.openai.com`** | 443 | OpenAI **`text-embedding-3-small`** — embeddings that power precedent retrieval (RAG) | If `RAG_EMBEDDING_PROVIDER=openai` (default) |
| `api.groq.com` | 443 | **Groq** LLM (Llama models) — alternative LLM provider | If `LLM_BASE_URL` points to Groq (base-compose default value) |

The app is **non-functional without one LLM host and one embeddings host**. At minimum whitelist the LLM host you set in `LLM_BASE_URL` **and** the embeddings host for `RAG_EMBEDDING_PROVIDER`.

### 3b. Conditional — only if you enable that feature/provider

| Host | Port | Enable only if |
|---|---|---|
| `api.cohere.com` | 443 | `RAG_EMBEDDING_PROVIDER=cohere` |
| `<resource>.openai.azure.com` | 443 | `RAG_EMBEDDING_PROVIDER=azure_openai` (Azure OpenAI embeddings) |
| `<resource>.search.windows.net` | 443 | `RAG_VECTOR_BACKEND=azure_search` (Azure AI Search vector store) |
| `*.pinecone.io` (e.g. `<index>.svc.<env>.pinecone.io`) | 443 | `RAG_VECTOR_BACKEND=pinecone` |
| `api.smith.langchain.com` | 443 | `LANGCHAIN_TRACING_V2=true` (LangSmith tracing — off by default) |
| `securetoken.googleapis.com`, `identitytoolkit.googleapis.com`, `*.googleapis.com` | 443 | Firebase auth is enabled (`firebase_service_account_path` set) — currently optional/unused |

> Default deployment (pgvector + OpenAI embeddings + Gemini-or-Groq LLM) needs **none** of §3b.

### 3c. Not needed at runtime (handled at build time)
- `openaipublic.blob.core.windows.net` — tiktoken's BPE vocabulary. The Dockerfile **pre-caches** it into the image (`TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache`), so the running container never calls it. The Bajaj firewall blocks it anyway.

### 3d. Production provider — Azure Microsoft AI Foundry (LLM + embeddings)

In production the **LLM is served exclusively** from this Bajaj Azure AI Foundry resource — it is the **only** LLM provider (Gemini / Groq / public OpenAI are dev-only and are not used in production):

**Production LLM Target URI (from Azure portal):**
`https://bl-prod02-opai-bajaj-compliance-app-01.openai.azure.com/openai/responses?api-version=2025-04-01-preview`

**The single host to whitelist:**

| Host | Port | Protocol | What uses it |
|---|---|---|---|
| `bl-prod02-opai-bajaj-compliance-app-01.openai.azure.com` | 443 | HTTPS | The production Azure OpenAI / AI Foundry resource — serves the LLM (and, if you put an embedding deployment on the same resource, the embeddings too). This is the only external egress the app needs. |

That is the entire production egress whitelist for the LLM. No `generativelanguage.googleapis.com`, no `api.groq.com`, no `api.openai.com`.

#### Config — wiring the app to Azure AI Foundry

Two halves, because the LLM path and the embeddings path are built differently in code:

**1) Embeddings + (optional) vector store — already Azure-native.** The app ships an `AzureOpenAIEmbedder` (uses the `AsyncAzureOpenAI` client) and an Azure AI Search store. Just set:

```bash
RAG_EMBEDDING_PROVIDER=azure_openai
AZURE_OPENAI_ENDPOINT=https://<resource-name>.openai.azure.com
AZURE_OPENAI_API_KEY=<azure-key>
AZURE_OPENAI_EMBED_DEPLOYMENT=<your-embedding-deployment-name>   # e.g. text-embedding-3-small
AZURE_OPENAI_API_VERSION=2024-02-01
RAG_EMBEDDING_DIM=1536          # keep 1536 for text-embedding-3-small (must match the ingested corpus)

# Optional — only if using Azure AI Search instead of pgvector:
# RAG_VECTOR_BACKEND=azure_search
# AZURE_SEARCH_ENDPOINT=https://<search-service>.search.windows.net
# AZURE_SEARCH_API_KEY=<search-admin-key>
```

> ⚠️ **Embedding dimension must match the knowledge base.** The precedent corpus is ingested with whatever model/dim was used at `ingest_knowledge_base` time. If you switch embedding providers, re-ingest (`docker exec compliance-backend python -m scripts.ingest_knowledge_base`) so query vectors and stored vectors are comparable. Staying on `text-embedding-3-small` (1536-dim) keeps it consistent with the OpenAI default.

**2) LLM — the production Azure resource (important wiring detail).** `llm_service.py` builds a **generic `AsyncOpenAI(base_url=...)` client** that calls **Chat Completions**, *not* the `AsyncAzureOpenAI` client and *not* the Responses API. The portal "Target URI" ends in `/openai/responses?api-version=...`, but you must **not** put that path in `LLM_BASE_URL`. Use the same resource's **OpenAI-compatible `/openai/v1/` surface** instead, and set `LLM_MODEL` to your **deployment name**:

```bash
LLM_BASE_URL=https://bl-prod02-opai-bajaj-compliance-app-01.openai.azure.com/openai/v1/
LLM_MODEL=<your-chat-deployment-name>     # the deployment name in the Foundry resource (e.g. gpt-4o), NOT the Target URI path
LLM_API_KEY=<AZURE_OPENAI_KEY>            # ⚠️ real key lives in .env (gitignored) / secrets — never commit it
LLM_INSECURE_TLS=false                     # keep TLS verification ON in production
```

- ✅ Works with the existing code **unchanged** because the Azure `/openai/v1/` route is OpenAI-wire-compatible (path, key auth, no per-call `api-version`). The host you whitelist is identical to the Target URI host.
- ⚠️ **Do not** put `/openai/responses?api-version=2025-04-01-preview` (the Responses API) or the classic `/openai/deployments/<dep>/...?api-version=...` form into `LLM_BASE_URL`. Both require switching `_build_client` in `llm_service.py` to `AsyncAzureOpenAI` (and, for Responses, a larger rewrite away from `chat.completions`). Only do that if the resource truly does not expose `/openai/v1/`.
- You need the **deployment name** for `LLM_MODEL`. The Target URI does not contain it — get it from the Foundry resource's *Deployments* tab in the Azure portal.
- The `health_check` calls `client.models.list()`; the `/openai/v1/` surface supports it. A failing health check is non-fatal but logs a warning.

**Embeddings in production:** the app still needs an embeddings provider for RAG (the LLM endpoint does not do embeddings). Cleanest is to deploy an embedding model (e.g. `text-embedding-3-small`) on the **same Azure resource** so there is still only one egress host — then set `RAG_EMBEDDING_PROVIDER=azure_openai`, `AZURE_OPENAI_ENDPOINT=https://bl-prod02-opai-bajaj-compliance-app-01.openai.azure.com`, `AZURE_OPENAI_API_KEY=<key>`, and `AZURE_OPENAI_EMBED_DEPLOYMENT=<embedding-deployment-name>` as in part (1) above. **Confirm an embedding deployment exists on the resource** — the URI you were given is a chat/responses deployment only.

---

## 4. Build-time egress — and how a pre-built image removes it

If you **build the images on the VM** (`docker compose up --build`), the VM additionally needs to reach the package and base-image registries below. Inside Bajaj these should come from the **internal Artifactory / registry mirror**, not the public internet.

| Host(s) | Used for | Needed by |
|---|---|---|
| `registry-1.docker.io`, `*.docker.io`, `production.cloudflare.docker.com` | Base images: `python:3.11-slim`, `pgvector/pgvector:pg15`, `redis:7-alpine`, `postgres:15-alpine`, Node base for the frontend | `docker build` / `docker compose pull` |
| `pypi.org`, `files.pythonhosted.org` | Python deps (`pip install -r requirements.txt`) | backend image build |
| `registry.npmjs.org` | Node deps (`npm install`) | frontend image build |
| `deb.debian.org` (+ security mirror) | OS packages (`build-essential`, `libpq-dev`, `poppler-utils`, `ca-certificates`) | backend image build |

---

## 5. Pre-built Docker image vs. building on the VM — which is easier?

**Short answer: yes, consuming a pre-built image is meaningfully easier and is the recommended path for the VM.** It collapses the whitelist down to just the §3 runtime endpoints.

**What you gain by shipping pre-built images** (build once on a machine that has access — e.g. via the internal mirror — push to the Bajaj internal registry, then `docker pull` on the VM):

- **No build-time egress.** Everything in §4 (Docker Hub, PyPI, npm, Debian apt, tiktoken blob) drops off the VM's whitelist entirely. The VM only needs the LLM + embeddings hosts in §3.
- **Faster, deterministic deploys.** No compilation (`build-essential`, native wheels like `psycopg2`, `umap-learn`, `scikit-learn`) on the VM; the same bytes that were tested are what run.
- **Smaller attack surface / no toolchain on the VM.** Build tooling and dev packages stay off the production host.
- **CA certs already baked in.** The corporate root CAs are registered during build, so TLS-to-the-proxy works on first boot.

**Caveats to plan for:**

1. **CPU architecture must match.** Build the images for the VM's architecture (almost certainly `linux/amd64`). Building on an ARM Mac requires `docker buildx --platform linux/amd64`.
2. **The frontend image is environment-specific.** The frontend bakes its API base URL **at build time** (`NEXT_PUBLIC_API_BASE` / `INTERNAL_API_BASE` build args in `docker-compose.yml`). If the VM serves the app on a hostname other than `localhost:8000`, rebuild the frontend image with the correct values — you can't just re-point it via runtime env for the browser-side calls.
3. **You still need a compose file (or equivalent) on the VM** to wire up Postgres, Redis, volumes, healthchecks, the backup sidecar, and the env vars. Replace the `build:` blocks with `image:` references to your internal registry tags. Postgres/Redis images are already pulled by tag (no build), so those are unaffected.
4. **Cert rotation = rebuild.** If the Cisco Umbrella / Bajaj root CAs rotate, the backend image must be rebuilt with new `certs/*.pem`.
5. **First-run data steps are unchanged** (see §6) — they run *inside* the already-built backend container, no internet needed beyond the LLM/embedding calls.

**Rule of thumb:** build images where you have mirror/internet access → push to the internal Docker registry → on the VM, `docker compose up -d` against image tags. The VM then only ever talks to the §3 runtime endpoints.

---

## 6. First-run steps on the VM (after `docker compose up -d`)

The backend runs `alembic upgrade head` automatically on boot (schema migrations). Then seed the rules and ingest the precedent corpus **once**:

```bash
# Seed starter rules (IRDAI / Bajaj brand / SEBI)
docker exec compliance-backend python -m scripts.seed_rules

# Ingest the reviewer-decision corpus that powers grading (calls the embeddings API)
docker exec compliance-backend python -m scripts.ingest_knowledge_base
```

Compose mounts `./dataset` and `./docs` read-only into the backend; the `compliance-backup` sidecar dumps Postgres daily (14-day retention).

**Required env vars** (set in `.env` on the VM): `LLM_API_KEY` (LLM provider key; may be a comma-list for failover), and an embeddings key — `OPENAI_API_KEY` for the default. Optional: `LLM_BASE_URL`, `LLM_MODEL`, `RAG_EMBEDDING_PROVIDER`, and the provider-specific keys in §3b.

---

## 7. Backend API endpoints (served on port 8000)

Routes are mounted at the **root** (no `/api` prefix). Interactive docs live at `http://<vm>:8000/docs`.

| Prefix | Methods / paths | Purpose |
|---|---|---|
| `/submissions` | `POST`, `GET`, `GET /{id}`, `DELETE /{id}`, `GET /{id}/similar` | Create/list/fetch/delete drafts; find similar past submissions |
| `/compliance` | `POST /analyze/{id}` (async), `/analyze/{id}/sync`, `/analyze/{id}/stream`, `GET /results/{id}`, `GET /check/{id}` | Run the compliance analysis and fetch results |
| `/chat` | `POST`, `POST /quote-violation`, `POST /suggest-rewrite` | Q&A and compliant-rewrite suggestions |
| `/rules` | `POST`, `GET`, `GET /{id}`, `PATCH /{id}`, `DELETE /{id}`, `POST /generate-from-document` | Manage the rule corpus |
| `/dashboard` | `GET /summary`, `/timeseries`, `/top-rules`, `/violations-by-category`, `/violations-by-severity` | Analytics for the dashboard |
| `/knowledge-base` | `POST /ingest`, `GET /stats`, `GET /search`, `GET /projection` | Manage/inspect the precedent vector store |
| `/health/rag`, `/debug/rag/search` | `GET` / `POST` | RAG health + debug |

Endpoints that call the paid LLM are rate-limited per IP (`HTTP_RATE_LIMIT_PER_MIN`, default 30/min).

---

## 8. Quick checklist for the infra/security request

- [ ] Open inbound **3000/tcp** and **8000/tcp** on the internal network (not internet).
- [ ] Keep 5432 / 6379 container-internal (do **not** expose).
- [ ] **Production:** whitelist outbound **443** to the single host `bl-prod02-opai-bajaj-compliance-app-01.openai.azure.com` — the only LLM provider (and embeddings, if deployed on the same resource). See §3d.
- [ ] **Production config:** `LLM_BASE_URL=https://bl-prod02-opai-bajaj-compliance-app-01.openai.azure.com/openai/v1/` (NOT the `/openai/responses` Target URI), `LLM_MODEL`=deployment name, key in `.env` only. For embeddings: `RAG_EMBEDDING_PROVIDER=azure_openai` + `AZURE_OPENAI_*` vars (§3d).
- [ ] *(Dev / non-Azure only)* whitelist your dev LLM host (`generativelanguage.googleapis.com` **or** `api.groq.com`) **and** `api.openai.com` for embeddings.
- [ ] Add §3b hosts only for any optional providers you enable.
- [ ] Prefer **pre-built images from the internal registry** → drop all §4 build hosts from the VM's whitelist.
- [ ] **Delete/rename `docker-compose.override.yml`** before starting on the VM.
- [ ] Ensure the corporate CA certs in `backend/certs/` are current (TLS inspection).
```
