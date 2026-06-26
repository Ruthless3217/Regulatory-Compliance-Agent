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
| **`generativelanguage.googleapis.com`** | 443 | Google **Gemini** — OpenAI-compatible LLM (dev fallback only) | If `LLM_BASE_URL` points to Gemini |
| **`api.openai.com`** | 443 | OpenAI **`text-embedding-3-small`** — embeddings (dev fallback) | If `RAG_EMBEDDING_PROVIDER=openai` |

The app is **non-functional without one LLM host and one embeddings host**. At minimum whitelist the LLM host you set in `LLM_BASE_URL` **and** the embeddings host for `RAG_EMBEDDING_PROVIDER`.

### 3b. Conditional — only if you enable that feature/provider

| Host | Port | Enable only if |
|---|---|---|
| `<resource>.openai.azure.com` | 443 | `RAG_EMBEDDING_PROVIDER=azure_openai` (Azure OpenAI embeddings — alternative) |
| `<resource>.search.windows.net` | 443 | `RAG_VECTOR_BACKEND=azure_search` (Azure AI Search vector store) |
| `api.smith.langchain.com` | 443 | `LANGCHAIN_TRACING_V2=true` (LangSmith tracing — off by default) |
| `securetoken.googleapis.com`, `identitytoolkit.googleapis.com`, `*.googleapis.com` | 443 | Firebase auth is enabled (`firebase_service_account_path` set) — currently optional/unused |

> The **production** default (`azure_cohere` embeddings via Azure AI Foundry) is covered by §3d, not §3b.

### 3c. Not needed at runtime (handled at build time)
- `openaipublic.blob.core.windows.net` — tiktoken's BPE vocabulary. The Dockerfile **pre-caches** it into the image (`TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache`), so the running container never calls it. The Bajaj firewall blocks it anyway.

### 3d. Production provider — Azure AI Foundry (LLM + Cohere embeddings)

In production both the LLM and the embeddings come from a single Bajaj Azure AI
Foundry project (`bl-bajaj-compliance-resource`). Gemini / Groq / public OpenAI /
public Cohere are dev-only and are **not** used in production.

The project exposes **two host surfaces**, and the app uses both:

| Host | Port | Protocol | What uses it |
|---|---|---|---|
| `bl-bajaj-compliance-resource.openai.azure.com` | 443 | HTTPS | Azure OpenAI surface — the **LLM** (`gpt-5.4` grading + `gpt-5.4-nano` critic). |
| `bl-bajaj-compliance-resource.services.ai.azure.com` | 443 | HTTPS | Azure AI Foundry **model-inference** surface — **Cohere embed v3** (and rerank v4). Cohere is NOT served on the `.openai.azure.com` host. |

That is the entire production egress whitelist. No `generativelanguage.googleapis.com`, no `api.groq.com`, no `api.openai.com`, no `api.cohere.com`.

#### Config — wiring the app to Azure AI Foundry

**1) LLM — Azure OpenAI surface.** `llm_service.py` uses the `AsyncAzureOpenAI`
client (selected by `LLM_PROVIDER=azure`). `LLM_BASE_URL` is the resource
**root** — no `/openai/v1` or `/openai/responses` path; the SDK builds the
`/openai/deployments/<model>/...?api-version=` path itself. `LLM_MODEL` and
`LLM_CLASSIFY_MODEL` are **deployment names**.

```bash
LLM_PROVIDER=azure
LLM_BASE_URL=https://bl-bajaj-compliance-resource.openai.azure.com
LLM_MODEL=gpt-5.4
LLM_CLASSIFY_MODEL=gpt-5.4-nano          # cheap/fast critic pass
LLM_API_KEY=<AZURE_OPENAI_KEY>           # ⚠️ real key lives in .env (gitignored) — never commit it
LLM_AZURE_API_VERSION=2025-04-01-preview
LLM_USE_MAX_COMPLETION_TOKENS=true       # gpt-5.4 is a reasoning model
LLM_SUPPORTS_TEMPERATURE=false
LLM_REASONING_EFFORT=low
LLM_INSECURE_TLS=false                    # keep TLS verification ON in production
```

**2) Embeddings — Cohere embed v3 via Foundry inference.** The app ships an
`AzureCohereEmbedder` (uses the `azure-ai-inference` SDK against the
`.services.ai.azure.com/models` host). Output is **1024-dim** — identical to
public `embed-english-v3.0`, so an index built against the public Cohere API
stays compatible (no re-ingest for the english variant).

```bash
RAG_EMBEDDING_PROVIDER=azure_cohere
RAG_EMBEDDING_DIM=1024
AZURE_INFERENCE_ENDPOINT=https://bl-bajaj-compliance-resource.services.ai.azure.com/models
AZURE_INFERENCE_API_KEY=                  # blank → reuse LLM_API_KEY (same resource)
AZURE_INFERENCE_API_VERSION=2024-05-01-preview
AZURE_COHERE_EMBED_DEPLOYMENT=<cohere-embed-v3-deployment-name>
```

> ⚠️ **Embedding dimension must match the knowledge base.** The precedent corpus
> is ingested with whatever model/dim was used at `ingest_knowledge_base` time.
> Cohere embed v3 is 1024-dim; if the index was built on `embed-english-v3.0`
> (also 1024-dim) it stays compatible. If you switch to a different model/dim,
> re-ingest: `docker exec compliance-backend python -m scripts.ingest_knowledge_base`.

> The reasoning LLM's `health_check` calls `client.models.list()`, which Azure
> does not expose at the resource root — the probe logs an info line and treats
> the deployment as available (chat calls are verified at runtime).

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
- [ ] **Production:** whitelist outbound **443** to **two** hosts on the `bl-bajaj-compliance-resource` Foundry project: `bl-bajaj-compliance-resource.openai.azure.com` (LLM) and `bl-bajaj-compliance-resource.services.ai.azure.com` (Cohere embed/rerank). See §3d.
- [ ] **Production config:** `LLM_PROVIDER=azure`, `LLM_BASE_URL=https://bl-bajaj-compliance-resource.openai.azure.com` (resource root, no path), `LLM_MODEL=gpt-5.4`, `LLM_CLASSIFY_MODEL=gpt-5.4-nano`, key in `.env` only. For embeddings: `RAG_EMBEDDING_PROVIDER=azure_cohere` + `AZURE_INFERENCE_*` / `AZURE_COHERE_EMBED_DEPLOYMENT` (§3d).
- [ ] *(Dev / non-Azure only)* whitelist your dev LLM host (`generativelanguage.googleapis.com`) **and** `api.openai.com` for embeddings.
- [ ] Add §3b hosts only for any optional providers you enable.
- [ ] Prefer **pre-built images from the internal registry** → drop all §4 build hosts from the VM's whitelist.
- [ ] **Delete/rename `docker-compose.override.yml`** before starting on the VM.
- [ ] Ensure the corporate CA certs in `backend/certs/` are current (TLS inspection).
```
