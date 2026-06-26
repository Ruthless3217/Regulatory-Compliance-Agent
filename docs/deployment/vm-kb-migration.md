# Migrating the KB + app to the VM (Cohere embed-v3, no re-embed)

This runbook moves the **knowledge base and all "necessary stuff"** from a build
machine (laptop) to the target **VM**, *without regenerating embeddings*.

> **Why no re-embed:** the corpus is embedded with **Cohere `embed-multilingual-v3.0`
> @ 1024 dimensions**. v3 dimensions are **fixed** — you cannot increase them
> (the only other v3 variant is the *light* model at 384, which is *smaller*).
> Configurable/larger dims (256/512/1024/**1536**) exist only in **embed-v4.0**.
> Because the model and dimension are unchanged, the vectors already in Postgres
> are valid on the VM — a DB transfer carries them over intact. Re-embedding would
> only be required if you switched model (e.g. to v4) or changed the dimension.

---

## What actually has to move (and what a DB dump does NOT cover)

A DB transfer is necessary but **not sufficient**. Three buckets must all land on
the VM:

| Bucket | What | How it moves | In git? |
|--------|------|--------------|---------|
| **1. Database** | RAG corpus + **embeddings**, rules, precedent_cases, product docs, examples, audit/feedback, adaptive weights | `pg_dump` → restore **or** `kb_embeddings_backup.py` bundle | ❌ (dump is a file you copy) |
| **2. Filesystem KB** | `backend/data/disclaimers/*.json` (10), `backend/data/product_fact_cards/*.json` (45) — read from disk at runtime | repo clone / image | ✅ already tracked |
| **3. Config / secrets** | `backend/.env` — Azure OpenAI + Cohere keys, embedding deployment, DB creds | **copy by hand** (gitignored) | ❌ never commit |

Plus the **pgvector extension** must exist on the target Postgres (use the
`pgvector/pgvector:pg15` image — a plain `postgres:15` will fail the `vector`
restore).

---

## Decide your DB transfer method

| Method | Use when | Notes |
|--------|----------|-------|
| **A. Full `pg_dump`** (recommended for a fresh VM) | Target DB is **empty/new** | Captures *everything* — all tables, audit history, feedback, adaptive weights. Simplest, most complete. |
| **B. `kb_embeddings_backup.py` bundle** | Target DB is **shared** or already has data | Idempotent upsert of only the 6 embedding tables — never deletes others' rows. Restores vectors verbatim, zero embedding calls. |

For a clean VM migration, use **A**. Keep **B** in your back pocket for topping up
a shared DB later.

---

## Path A — full database dump → restore (fresh VM)

### A1. On the build machine — produce a fresh, *verified* dump

> ⚠️ Do **not** ship the newest file blindly. The latest dump
> (`compliance_db_20260626T060600Z.sql.gz`) is **~1 KB = a failed/empty dump**.
> Always check the size first. The last known-good full dump is
> `compliance_db_20260624T103230Z.sql.gz` (~38 MB).

```bash
# Make a fresh dump from the running stack (sidecar already has pg_dump):
docker compose exec postgres sh -c \
  'pg_dump -U compliance_user -d compliance_db --no-owner --no-privileges --no-comments' \
  | gzip -9 > compliance_db_migrate.sql.gz

# Sanity-check it is real (should be tens of MB, not KB):
ls -lh compliance_db_migrate.sql.gz
gzip -t compliance_db_migrate.sql.gz && echo "gzip OK"
```

### A2. Move three things to the VM

```bash
scp compliance_db_migrate.sql.gz   user@vm:/srv/cie/
scp backend/.env                   user@vm:/srv/cie/backend/.env   # secrets — by hand
# the repo itself (code + backend/data/*) via git clone or the offline image bundle:
#   - online VM:  git clone <repo> /srv/cie  &&  git checkout <branch>
#   - air-gapped: follow docs/deployment/offline-docker-deploy.md (docker save/load)
```

### A3. On the VM — bring up Postgres (pgvector) and restore

```bash
cd /srv/cie
docker compose up -d postgres        # MUST be the pgvector/pgvector:pg15 image
# wait until healthy:
docker compose exec postgres pg_isready -U compliance_user -d compliance_db

# restore via the helper (validates size + gzip, then prints a post-restore
# summary incl. embedding provenance). Mount/copy the dump where the script can
# see it (the backup sidecar already mounts ./backend/backups -> /backups):
docker compose exec db-backup sh /scripts/db_restore.sh /backups/compliance_db_migrate.sql.gz

# --- or, without the helper, a one-liner straight into psql: ---
gunzip -c compliance_db_migrate.sql.gz \
  | docker compose exec -T postgres psql -U compliance_user -d compliance_db
```

> **"Same dimension" ≠ "same model."** Migration `0009` stamped every vector with
> the `embedding_model` that produced it. `db_restore.sh` prints a provenance
> breakdown at the end — if more than one `(model, dim)` row appears, the corpus
> is **mixed-model**: those vectors aren't comparable even at equal dimension, and
> the backend's fail-closed guard will drop the off-model ones at query time. The
> fix is a single-model re-embed (all Cohere v3). Check it directly any time with:
>
> ```sql
> SELECT coalesce(embedding_model,'(null)'), coalesce(embedding_dim, vector_dims(embedding)), count(*)
> FROM rag_chunks WHERE embedding IS NOT NULL GROUP BY 1,2 ORDER BY 3 DESC;
> ```

If the dump predates a migration, let the backend apply the rest on startup
(`alembic upgrade head` runs at boot — see offline-docker-deploy.md §Notes).

### A4. Bring up the rest and verify (see "Verification" below)

```bash
docker compose up -d
```

---

## Path B — portable embeddings bundle (shared / incremental DB)

Run from `backend/` (or inside the backend container). This restores the stored
1024-dim vectors **verbatim — zero Cohere calls**.

```bash
# build machine — export the 6 embedding tables to a bundle dir
cd backend
python -m scripts.kb_embeddings_backup export --out ./kb_snapshot
#  exported   N rows  rag_rules ...  -> rag_rules.jsonl.gz   (etc.)

# move the bundle
scp -r backend/kb_snapshot  user@vm:/srv/cie/backend/kb_snapshot

# VM — migrations must already be applied (tables must exist), then:
cd backend
python -m scripts.kb_embeddings_backup verify  --in ./kb_snapshot   # bundle vs live counts
python -m scripts.kb_embeddings_backup restore --in ./kb_snapshot   # idempotent upsert
python -m scripts.kb_embeddings_backup verify  --in ./kb_snapshot   # confirm parity
```

Tables covered: `rag_rules`, `rag_chunks`, `rag_source_docs`,
`rag_compliance_examples`, `rag_product_docs`, `precedent_cases`.
(BM25 `search_tsv` is recomputed by the table trigger on insert — not shipped.)

---

## VM `.env` — the settings that MUST match the stored vectors

The machine that *queries* must embed the query with the **same model** that
produced the stored vectors, or retrieval silently degrades. Confirm on the VM:

```ini
RAG_EMBEDDING_PROVIDER=azure_cohere
RAG_EMBEDDING_MODEL=embed-english-v3.0          # v3
RAG_EMBEDDING_DIM=1024                            # fixed for v3 — do not change
AZURE_COHERE_EMBED_DEPLOYMENT=Cohere-embed-v3-multilingual   # same deployment that built the corpus
AZURE_COHERE_RERANK_DEPLOYMENT=Cohere-rerank-v4.0-fast        # reranker is runtime-only, nothing to migrate
AZURE_INFERENCE_ENDPOINT=https://<your-foundry-resource>.services.ai.azure.com/models
AZURE_INFERENCE_API_KEY=...                       # secret
COHERE_API_KEY=...                                # secret
DATABASE_URL=postgresql://compliance_user:...@postgres:5432/compliance_db
```

> The **reranker does not migrate.** A reranker stores nothing; it runs live at
> query time over retrieved candidates. Just point `.env` at the rerank
> deployment and it works.

---

## Verification (run after either path)

```bash
# 1. pgvector extension present
docker compose exec postgres psql -U compliance_user -d compliance_db \
  -c "SELECT extname, extversion FROM pg_extension WHERE extname='vector';"

# 2. embeddings actually landed (counts > 0, dim = 1024)
docker compose exec postgres psql -U compliance_user -d compliance_db -c "
  SELECT 'rag_chunks' t, count(*) FROM rag_chunks
  UNION ALL SELECT 'rag_rules', count(*) FROM rag_rules
  UNION ALL SELECT 'precedent_cases', count(*) FROM precedent_cases
  UNION ALL SELECT 'rag_product_docs', count(*) FROM rag_product_docs;"

# 3. a stored vector is the expected width (should print 1024)
docker compose exec postgres psql -U compliance_user -d compliance_db \
  -c "SELECT vector_dims(embedding) FROM rag_chunks WHERE embedding IS NOT NULL LIMIT 1;"

# 4. filesystem KB present in the container
docker compose exec backend sh -c 'ls /app/data/disclaimers | wc -l; ls /app/data/product_fact_cards | wc -l'
#   expect 10 and 45

# 5. end-to-end: API up, a real retrieval returns hits
curl -s http://localhost:8000/docs >/dev/null && echo "API up"
```

A clean run = extension present, non-zero counts, `vector_dims = 1024`, 10 + 45
data files, API reachable.

---

## Summary

- **No re-embedding** — you stay on Cohere v3 @ 1024; embeddings are portable.
- **v3 dimension cannot be increased** — 1024 is fixed; only v4 offers larger/
  configurable dims (a separate, full re-ingest project if you ever want it).
- Move **three** things, not one: the **DB dump**, the **repo (incl. `backend/data/*`)**,
  and **`.env`** (by hand).
- Target Postgres must be **pgvector**, not plain postgres.
- The **reranker is runtime-only** — nothing to migrate.
- Don't trust the newest dump file by size alone — the 2026-06-26 one is empty.
