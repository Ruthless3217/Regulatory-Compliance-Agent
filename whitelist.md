# Isolated-VM Deployment Whitelist & Network Guide

> For deploying the Regulatory Compliance Agent on an **in-house VM with no/limited internet**.
> This file lists every external endpoint the system touches, separates **build-time** from
> **run-time**, separates **mandatory** from **optional**, and maps the git history to what is
> easy vs. hard to deploy. Last reviewed: 2026-06-03.

---

## 0. TL;DR — minimum to run

At **runtime**, with the recommended config, the backend needs egress to **exactly 2 external hosts** (both HTTPS/443):

| What | Host (depends on provider) | Why |
|------|----------------------------|-----|
| **LLM** | `api.groq.com` *(your `.env` **and** `docker-compose.yml` default)* **or** `generativelanguage.googleapis.com` *(`docker-compose.override.yml` dev default)* | Grades each chunk; chat; rule generation |
| **Embeddings** | `api.cohere.com` *(your `.env`)* | Vectorizes chunks/queries for precedent retrieval |

Everything else (Postgres, Redis, frontend, scoring, vector projection, preprocessing) runs **inside the VM with no internet**. Disable LangSmith tracing and the system needs nothing else at runtime.

> ⚠️ If you cannot open **any** outbound HTTPS, the app cannot do its core job — the LLM and the embedding model are hosted SaaS. The only way to run fully air-gapped is to self-host an LLM + embedding model inside the VM (see §7).

---

## 1. Run-time external endpoints (what to whitelist on the VM firewall)

All are **HTTPS / TCP 443**. Whitelist by **DNS name** (these are SaaS with rotating IPs — do not pin IPs).

### 1a. MANDATORY (pick one per row, based on env)

| Capability | Env selector | Endpoint to whitelist | Notes |
|-----------|--------------|------------------------|-------|
| LLM (analysis/chat/rule-gen) | `LLM_BASE_URL` + `LLM_MODEL` | **Groq**: `api.groq.com` | Current `.env` **and** `docker-compose.yml` default (model `meta-llama/llama-4-scout-17b-16e-instruct`). |
| | | **Gemini**: `generativelanguage.googleapis.com` | `docker-compose.override.yml` **dev** default (`gemini-2.0-flash`). |
| | | **OpenAI**: `api.openai.com` | If you point `LLM_BASE_URL` there. |
| Embeddings | `RAG_EMBEDDING_PROVIDER` | **Cohere**: `api.cohere.com` (and `api.cohere.ai`) | Current `.env` (`embed-english-v3.0`, 1024-dim). |
| | | **OpenAI**: `api.openai.com` | `text-embedding-3-small`, 1536-dim. |
| | | **Azure OpenAI**: `<your-resource>.openai.azure.com` | Only if `RAG_EMBEDDING_PROVIDER=azure_openai`. |

> You only need the **one LLM host** and **one embeddings host** matching your env. With the current `.env` that is `api.groq.com` + `api.cohere.com`.

### 1b. OPTIONAL / CONDITIONAL — avoid these to reduce whitelisting

| Capability | When it calls out | Endpoint | How to avoid |
|-----------|-------------------|----------|--------------|
| **LangSmith tracing** | Only if `LANGCHAIN_TRACING_V2=true` | `api.smith.langchain.com` | **Set `LANGCHAIN_TRACING_V2=false`** (default in compose). Also a data-egress concern — it ships document content off-box. Recommended OFF in the VM. |
| **tiktoken BPE download** | First chunking call, only if not cached | `openaipublic.blob.core.windows.net` | **Already pre-cached in the Docker build** (`TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache`). Code also **falls back to paragraph chunking** if blocked. No whitelist needed if you build the image normally. |
| **Next.js telemetry** | Build-time only, at `npm run build` | `telemetry.nextjs.org` | **Already disabled** — `NEXT_TELEMETRY_DISABLED=1` is set in `frontend/Dockerfile` (build + runtime stages). No runtime egress; no whitelist needed. |
| **Pinecone** (alt vector store) | Only if `RAG_VECTOR_BACKEND=pinecone` | `*.pinecone.io` | Don't use — keep `RAG_VECTOR_BACKEND=pgvector` (in-VM). |
| **Azure AI Search** (alt vector store) | Only if `RAG_VECTOR_BACKEND=azure_search` | `<svc>.search.windows.net` | Don't use — keep pgvector. |

### 1c. INSTALLED-BUT-UNUSED SDKs (no runtime egress)
- `firebase-admin` — **not imported anywhere** in `app/` (auth is not wired). No egress.
- `google-generativeai` — **not imported**; Gemini is reached via the OpenAI-compatible client + `base_url`, not this SDK. No egress unless you wire it.
- These add image size but make **zero network calls**.

---

## 2. Internal services (inside the VM, NO internet)

These talk container-to-container on the Docker network — whitelist **nothing** externally for them:

| Service | Image | Port | Notes |
|---------|-------|------|-------|
| Postgres + pgvector | `pgvector/pgvector:pg15` | 5432 | Primary DB + vector store. In-VM. |
| Redis | `redis:7-alpine` | 6379 | LangGraph checkpoints. **Optional** — code falls back to in-memory `MemorySaver` if Redis is down. |
| Backend (FastAPI) | built locally | 8000 | |
| Frontend (Next.js) | built locally | 3000 | SSR proxies to backend via internal hostname `backend:8000` (see `next.config.ts` rewrites). |
| DB backup sidecar | `postgres:15-alpine` | — | Daily `pg_dump`. In-VM. |

---

## 3. BUILD-TIME egress (needed to BUILD the images, not to run them)

If the VM builds images itself, it needs these **during `docker build` only**. Best practice: **build the images on a machine that has internet (or via your internal mirror), then ship the images to the VM** so the VM needs none of these.

| Purpose | Endpoint(s) | Used by |
|---------|-------------|---------|
| Docker base images | `registry-1.docker.io`, `docker.io`, `*.docker.io`, `production.cloudflare.docker.com` | `python:3.11-slim`, `node:20-slim`, `pgvector/pgvector:pg15`, `redis:7-alpine`, `postgres:15-alpine` |
| Python packages | `pypi.org`, `files.pythonhosted.org` | `pip install -r requirements.txt` |
| Node packages | `registry.npmjs.org` | frontend `npm install --legacy-peer-deps` |
| Next.js telemetry | `telemetry.nextjs.org` | `npm run build` — **disabled** via `NEXT_TELEMETRY_DISABLED=1`, so not actually contacted |
| OS packages (apt) | `deb.debian.org`, `security.debian.org` | `build-essential`, `libpq-dev`, `poppler-utils`, `ca-certificates` |
| tiktoken BPE (pre-cache) | `openaipublic.blob.core.windows.net` | One-time during build; failure is non-fatal |

> **Recommended:** point pip/npm/apt/docker at your **internal artifact mirrors** (Nexus/Artifactory/internal registry) and pre-build images. Then build-time egress = 0.

---

## 4. TLS / corporate proxy note (important for Bajaj)

The backend `Dockerfile` already registers **Bajaj root CA + Cisco Umbrella intermediates** into both the OS trust store and certifi's bundle, because Cisco Umbrella SIG intercepts outbound HTTPS. The files baked in are `backend/certs/`: `bajaj-root.pem` (+ `.cer`), `cisco-umbrella-root.pem`, `cisco-umbrella-primary.pem`, `cisco-umbrella-secondary.pem`. If your VM sits behind the same inspection:
- Keep those `certs/*.pem` current, or
- the Python SDKs (openai, cohere, langsmith) will fail with `CERTIFICATE_VERIFY_FAILED`.
- There is an escape hatch `LLM_INSECURE_TLS=true` (disables verification) — **avoid in production**; fix the CA trust instead.

If the VM reaches the APIs **directly** (no inspection proxy), the bundled CAs are harmless.

---

## 5. Provider-choice matrix (fewest external dependencies)

| Config knob | Value | External hosts added |
|-------------|-------|----------------------|
| `LLM_BASE_URL` | Groq / Gemini / OpenAI | 1 (one of `api.groq.com` / `generativelanguage.googleapis.com` / `api.openai.com`) |
| `RAG_EMBEDDING_PROVIDER` | `cohere` / `openai` / `azure_openai` | 1 (one of `api.cohere.com` / `api.openai.com` / `*.openai.azure.com`) |
| `RAG_VECTOR_BACKEND` | **`pgvector`** (keep this) | 0 (in-VM) |
| `LANGCHAIN_TRACING_V2` | **`false`** (set this) | 0 |

**Recommended VM config = 2 external hosts total.** If LLM and embeddings are the **same vendor** (e.g. OpenAI for both), it collapses to **1 host** (`api.openai.com`).

---

## 6. Deployment difficulty by phase (from git history)

Mapping the commit history to deploy effort in an isolated VM:

| Phase | Representative commits | Needs external egress? | Deploy difficulty |
|-------|------------------------|------------------------|-------------------|
| **0. Infra scaffold** (FastAPI, Postgres, Redis, frontend, migrations) | `9b124d4 … 858f7d2` | **No** (all in-VM) | 🟢 Easy — works fully offline once images are present |
| **1. Core compliance engine + rules + dashboard** | `ce93697` | **LLM host only** (analysis/chat/rule-gen) | 🟡 Needs 1 host whitelisted |
| **2. RAG pipeline + precedent KB + ingestion** | `726ab89 … f356c40`, `07cb866` | **Embeddings host** (+ pgvector in-VM) | 🟡 Needs 1 host whitelisted |
| **3. Per-chunk precedent analysis in graph + scoring** | `5e6c289 … 55c8537` | **LLM + embeddings** (the full path) | 🟡 Both hosts; this is the real workload |
| **4. Eval harness + vector-space projection (umap/sklearn)** | `10d6bad`, `47d30f4`, `fc3cf29` | **No** (pure local compute) | 🟢 Easy — umap/sklearn run in-VM, no egress |
| **5. Reviewer-voice + real corpus import** | `9b772c5 …`, `8e898de`, `5c6461b` | Embeddings (to ingest corpus) | 🟡 One-time ingest needs embeddings host |
| **(current) Fail-closed + security hardening branch** | `feat/precedent-compliance-engine` | Same as above | 🟡 No new external deps |

### What deploys with ease (no external whitelisting beyond build)
- The whole **platform skeleton**: DB, Redis, frontend, API, auth-less routes, file upload/preprocessing (tiktoken pre-cached or paragraph fallback), **scoring**, **vector-space projection** (umap/sklearn are local).
- You can stand up the app, browse the UI, ingest already-embedded data, and view dashboards **fully offline**.

### What needs whitelisting
- **Any actual compliance analysis or chat** → the **LLM host**.
- **Any retrieval or fresh corpus ingestion** → the **embeddings host**.
- That's it. Two DNS names.

---

## 7. Fully air-gapped option (zero external runtime egress)

If the VM must have **no** outbound internet at all, replace the two SaaS calls with in-VM models:
- **LLM:** run a local OpenAI-compatible server (e.g. vLLM / Ollama / llama.cpp server) inside the VM and point `LLM_BASE_URL` at `http://localhost:<port>/v1`. The code already speaks the OpenAI protocol, so no code change — just the base URL + model name.
- **Embeddings:** this needs a small code addition — a local embedder implementing the `Embedder` protocol (`app/services/rag/ports.py`) backed by a local sentence-transformers / BGE model, registered in `app/services/rag/factory.py`. (Today only OpenAI/Azure/Cohere embedders exist.)
- Keep `RAG_VECTOR_BACKEND=pgvector`, `LANGCHAIN_TRACING_V2=false`.
- Result: **0 external hosts** at runtime. Effort: ~half a day for the local-embedder adapter + GPU/CPU sizing for the models.

---

## 8. Pre-flight checklist for the isolated VM

- [ ] Build images on an internet-connected host (or internal mirror); load them onto the VM (`docker load`).
- [ ] Confirm corporate CA certs in `backend/certs/` are current (if behind Cisco/Umbrella inspection).
- [ ] Set env: `RAG_VECTOR_BACKEND=pgvector`, `LANGCHAIN_TRACING_V2=false`.
- [ ] Whitelist (DNS, 443): your **LLM host** + your **embeddings host** (2 names; see §1a).
- [ ] Verify tiktoken cache baked into the image (`/opt/tiktoken-cache`) — otherwise whitelist `openaipublic.blob.core.windows.net` or accept paragraph-chunking fallback.
- [ ] Postgres/Redis run in-VM — no external rules needed. (Redis optional.)
- [ ] Run `alembic upgrade head` (the backend container does this on start).
- [ ] Smoke test: submit a short document → expect a graded result. If it hangs/fails on analysis, the LLM/embeddings host whitelist is the first thing to check.

---

## 9. Quick reference — copy/paste firewall allowlist (current `.env` providers)

```
# Runtime (HTTPS/443) — current Groq + Cohere config:
api.groq.com
api.cohere.com
api.cohere.ai

# Only if LangSmith tracing is left ON (recommend OFF):
# api.smith.langchain.com

# Only if tiktoken not pre-cached in the image:
# openaipublic.blob.core.windows.net

# Build-time only (better: use internal mirrors) —
# docker.io, registry-1.docker.io, pypi.org, files.pythonhosted.org,
# registry.npmjs.org, deb.debian.org, security.debian.org
```
