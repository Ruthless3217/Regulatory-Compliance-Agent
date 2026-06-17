from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List


class Settings(BaseSettings):
    # Database (Individual components)
    db_user: str = "compliance_user"
    db_pass: str = "compliance_pass"
    db_name: str = "compliance_db"
    db_host: str = "localhost"
    db_port: str = "5432"

    # This field will automatically pick up DATABASE_URL from environment
    database_url_env: str = Field("", alias="DATABASE_URL")

    @property
    def database_url(self) -> str:
        # 1. If DATABASE_URL is explicitly set, use it
        if self.database_url_env:
            return self.database_url_env
        # 2. Fallback to constructed URL
        return f"postgresql://{self.db_user}:{self.db_pass}@{self.db_host}:{self.db_port}/{self.db_name}"

    # LLM (Gemini via OpenAI-compatible API)
    # LLM_API_KEY may hold a single key OR a comma-separated list. Multiple keys
    # enable failover: when one Groq key hits its TPM/TPD ceiling the LLMService
    # rotates to the next (each Groq key carries an independent token budget).
    llm_api_key: str = ""
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    llm_model: str = "gemini-2.0-flash"
    llm_insecure_tls: bool = False  # set True to bypass TLS verify (e.g. behind Cisco SSL inspection)
    llm_max_tokens: int = 4096      # hard cap on generated tokens per call (cost-leak guard)

    # Provider selection. "" keeps the OpenAI-compatible path (Gemini/Groq/local
    # vLLM via base_url). Set LLM_PROVIDER=azure to route the analysis LLM through
    # AsyncAzureOpenAI — deployment-scoped path + ?api-version= + api-key header,
    # which native Azure OpenAI requires (a plain client 404s on /models and
    # /chat/completions at the resource root).
    llm_provider: str = ""                          # "" | azure | openai
    llm_azure_api_version: str = "2024-02-01"       # LLM_AZURE_API_VERSION (Azure only)

    # Reasoning models (gpt-5.x / o-series) shape requests differently: they
    # reject max_tokens (need max_completion_tokens), reject any custom
    # temperature, and accept a reasoning_effort knob. Defaults preserve the
    # classic chat-completions behaviour for Gemini/Groq.
    llm_use_max_completion_tokens: bool = False     # LLM_USE_MAX_COMPLETION_TOKENS
    llm_supports_temperature: bool = True           # LLM_SUPPORTS_TEMPERATURE
    llm_reasoning_effort: str = ""                  # LLM_REASONING_EFFORT: "" | minimal | low | medium | high

    @property
    def llm_is_azure(self) -> bool:
        """True when the analysis LLM should use the Azure OpenAI client.

        Triggered explicitly by LLM_PROVIDER=azure, or inferred from an Azure
        resource hostname in LLM_BASE_URL so a misconfigured provider field
        can't silently fall back to the wrong (404-ing) client.
        """
        if (self.llm_provider or "").strip().lower() == "azure":
            return True
        return ".openai.azure.com" in (self.llm_base_url or "").lower()

    @property
    def llm_api_keys(self) -> List[str]:
        """LLM_API_KEY parsed into an ordered, de-duplicated list of keys.

        A bare key (no comma) yields a one-element list, preserving prior
        single-key behaviour. Duplicates collapse because the same key is the
        same upstream account/budget — rotating between identical keys buys
        nothing and would corrupt per-key rate-limit accounting.
        """
        seen = set()
        keys: List[str] = []
        for raw in (self.llm_api_key or "").split(","):
            k = raw.strip()
            if k and k not in seen:
                seen.add(k)
                keys.append(k)
        return keys

    # Redis (LangGraph Persistence)
    redis_url: str = "redis://localhost:6379"

    # CORS - Allow frontend origins
    api_cors_origins: List[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://localhost:8080",
    ]

    # Application
    environment: str = "development"
    log_level: str = "INFO"
    # Per-IP per-minute cap on paid LLM endpoints (cost-explosion guard, H8).
    http_rate_limit_per_min: int = 30

    # File Upload
    max_upload_size: int = 52428800  # 50MB
    upload_dir: str = "./uploads"
    # Root that knowledge-base ingest is confined to. Any folder_path outside
    # this tree is rejected (prevents arbitrary server-side file read — audit C8).
    kb_ingest_root: str = "./uploads"

    # Firebase
    firebase_service_account_path: str = ""
    firebase_api_key: str = ""

    # LangSmith Tracing
    langchain_tracing_v2: str = "false"
    langchain_endpoint: str = "https://api.smith.langchain.com"
    langchain_api_key: str = ""
    langchain_project: str = "regulatory-compliance-agent"

    # RAG — pluggable backend
    rag_embedding_provider: str = "openai"        # openai | azure_openai | cohere
    rag_vector_backend: str = "pgvector"          # pgvector | azure_search | pinecone
    rag_embedding_model: str = "text-embedding-3-small"
    rag_embedding_dim: int = 1536
    rag_top_k_analysis: int = 8
    rag_top_k_chat: int = 5
    rag_top_k_similar: int = 3
    rag_score_threshold: float = 0.0              # 0.0 = no floor; RRF scores are unbounded-low
    # Minimum cosine similarity on the VECTOR leg before fusion. Unlike RRF
    # (unbounded), cosine is in [-1,1], so this is a meaningful relevance floor.
    # Candidates below it are dropped, so an unrelated chunk can legitimately
    # retrieve zero precedents → knowledge_base_empty → fail closed (audit C6).
    rag_min_cosine: float = 0.25
    rag_recall_pool: int = 30
    rag_rrf_k: int = 60
    rag_active_categories: List[str] = ["regulatory", "brand", "seo", "irdai", "sebi"]

    # Precedent compliance engine (Phase 1)
    pgvector_top_k: int = 15                      # precedents retrieved per chunk (Phase 1.5: filter-and-cite needs broader recall; LLM filters out non-applicable ones)
    kb_chunk_size: int = 500
    kb_chunk_overlap: int = 50
    kb_batch_size: int = 100
    kb_min_fuzzy_score: int = 60                  # rapidfuzz partial_ratio threshold
    viz_points_per_index: int = 2000              # projection point cap per index

    # OpenAI (direct API — v1 default for embeddings)
    openai_api_key: str = ""

    # Azure OpenAI (v2, embeddings)
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-02-01"
    azure_openai_embed_deployment: str = "text-embedding-3-small"

    # Azure AI Search (v2, vector store)
    azure_search_endpoint: str = ""
    azure_search_api_key: str = ""
    azure_search_rules_index: str = "rag-rules"
    azure_search_chunks_index: str = "rag-chunks"
    azure_search_source_docs_index: str = "rag-source-docs"

    # Cohere (alternative embeddings — 1024-dim)
    cohere_api_key: str = ""
    cohere_embedding_model: str = "embed-english-v3.0"
    # Per-text embedding cache (Priority 4d) — avoids re-embedding repeated chunks.
    embed_cache_size: int = 2048

    # Groq two-model strategy + token rate limiting (Priority 4).
    # classify = cheap/fast first pass, citation = stronger generation.
    groq_classify_model: str = "llama-3.1-8b-instant"
    groq_citation_model: str = "llama-3.3-70b-versatile"
    # Token ceilings per model (Groq free-tier defaults; override via env).
    groq_classify_tpm: int = 6000
    groq_classify_tpd: int = 500000
    groq_citation_tpm: int = 12000
    groq_citation_tpd: int = 100000
    # Queue a submission once daily usage crosses this fraction of the TPD cap.
    groq_daily_cap_fraction: float = 0.9
    # Max chunks graded concurrently. Each grading call is a large (precedents +
    # rules) prompt (~6-10k tokens); firing many at once bursts past Groq's
    # per-minute token limit (30k TPM free tier) → 429 → chunk fails → run fails
    # closed at "waiting_for_review". Keep this low (1-2) on the free tier so
    # calls fit under TPM; raise it on a paid/Dev tier. Override via env.
    grade_concurrency: int = 2

    # Pinecone (alternative v1 vector store)
    pinecone_api_key: str = ""
    pinecone_index_name: str = ""
    pinecone_namespace_rules: str = "rag_rules"
    pinecone_namespace_chunks: str = "rag_chunks"
    pinecone_namespace_srcdocs: str = "rag_source_docs"

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"
        env_prefix = ""


settings = Settings()
