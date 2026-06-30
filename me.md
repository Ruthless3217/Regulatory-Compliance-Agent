# Ingestion Migration & Model Optimization Status

This document provides a summary of the current session's progress, active configurations, and step-by-step instructions to resume work in a fresh context window.

---

## 1. Context & Goals
* **Goal:** Perform a clean database ingestion of all compliance resources (guidelines, precedents, brochures, and knowledge base decisions) from scratch.
* **Key Change:** Migrate embeddings from the old public Cohere API (`embed-english-v3.0`) to the **Microsoft Azure AI Foundry** inference endpoint using **`Cohere-embed-v3-multilingual`** (1024-dim).
* **Cost & Performance Optimization:** Route all heavy ingestion-time LLM operations (such as precedent comment normalization/enrichment and rule generation) to the cheaper **`gpt-5.4-nano`** model (via `critic_llm_service`) instead of the premium `gpt-5.4` model.

---

## 2. Completed Milestones
1. **Model & Environment Alignment:**
   * Restarted Docker containers to load the updated `.env` settings:
     * `RAG_EMBEDDING_PROVIDER=azure_cohere`
     * `RAG_EMBEDDING_MODEL=Cohere-embed-v3-multilingual`
     * `AZURE_INFERENCE_ENDPOINT=https://bl-bajaj-compliance-resource.services.ai.azure.com/models`
     * `CRITIC_LLM_MODEL=gpt-5.4-nano` (active for the critic/enrichment layer).
2. **Organized Datasets:**
   * Grouped all source ingestion files into [ingestion_dataset](file:///D:/Regulatory-Compliance-Agent/ingestion_dataset):
     * Guidelines docs: `ingestion_dataset/guidelines-docs/`
     * Raw Reviewer comment ledger: `ingestion_dataset/ledger/`
     * Raw Word ticket downloads: `ingestion_dataset/downloads/`
   * Added the folder mount in [docker-compose.yml](file:///D:/Regulatory-Compliance-Agent/docker-compose.yml):
     ```yaml
     - ./ingestion_dataset:/app/ingestion_dataset
     ```
3. **Guidelines Ingestion (Completed):**
   * Ran `ingest_guidelines.py` inside the container. It parsed the guideline markdown files and indexed **76 rules** into `rag_rules` using the new multilingual embeddings.
4. **Product Brochures Ingestion (Completed):**
   * Copied official brochures into the container's `/app/Broch/` directory and ran `ingest_product_brochures.py`. It processed 49 PDFs and indexed **45 brochures** (quarantined 4) into `rag_product_docs`.
5. **Raw Downloads Conversion (Completed):**
   * Ran `convert_downloads_to_corpus.py` to parse the 609 Word documents (`.docx`) in `docs/downloads` and generated **431 structured JSON files** (containing 5,401 reviewer decisions), staged inside the container at `/app/uploads/parsed_precedents`.

---

## 3. Current Code Customizations
The following optimized files have been modified on the host and staged in `backend/uploads/` so they can be copied into the container:
1. `backend/app/services/precedent/enrichment.py`: Imports and routes comment enrichment to `critic_llm_service` (`gpt-5.4-nano`).
2. `backend/app/services/rag/indexers/precedent_indexer.py`: Fixed syntax error on line 1.
3. `backend/scripts/ingest_precedent_cases.py`: Corrected to support container-specific CLI arguments.

---

## 4. Next Steps (Run in New Window)

### Step A: Sync staged files inside the container
Run the following commands to copy our optimized python files and product brochures from the host-mounted `/app/uploads/` directory to their correct locations in the newly recreated container:
```powershell
docker exec compliance-backend cp /app/uploads/enrichment.py /app/app/services/precedent/enrichment.py
docker exec compliance-backend cp /app/uploads/precedent_indexer.py /app/app/services/rag/indexers/precedent_indexer.py
docker exec compliance-backend cp /app/uploads/ingest_precedent_cases.py /app/scripts/ingest_precedent_cases.py
docker exec compliance-backend cp -r /app/uploads/Broch /app/Broch
```

### Step B: Reset Database Schema (From Scratch)
Before running the ingestions, wipe and migrate the Postgres database:
```powershell
docker exec compliance-backend alembic upgrade head
```

### Step C: Seed Rules
Seed the default system rules:
```powershell
docker exec compliance-backend python -m scripts.seed_rules
```

### Step D: Ingest Knowledge Base (From converted downloads)
Ingest the converted `.docx` precedents:
```powershell
docker exec compliance-backend python -m scripts.ingest_knowledge_base --folder /app/uploads/parsed_precedents
```

### Step E: Ingest Guidelines
Re-run Guidelines Ingestion (now that the DB is reset):
```powershell
docker exec compliance-backend python -m scripts.ingest_guidelines
```

### Step F: Ingest Product Brochures
Re-run Product Brochures Ingestion:
```powershell
docker exec compliance-backend python -m scripts.ingest_product_brochures /app/Broch --apply
```

### Step G: Ingest Precedent Cases (Comment Ledger)
Run Precedent Cases Ingestion (uses cached normalizations where available, else calls `gpt-5.4-nano`):
```powershell
docker exec compliance-backend python -m scripts.ingest_precedent_cases --ledger /app/docs/ledger/comment_ledger.jsonl --remediation /app/docs/ledger/remediation_pairs.jsonl --cache-dir /app/data/precedent_enrich_cache
```
