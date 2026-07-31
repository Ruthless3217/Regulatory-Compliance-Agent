from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from typing import List, Optional


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

    # --- Auth ---
    auth_enabled: bool = True
    auth_ip_binding_mode: str = "strict"          # strict | cidr | list | log_only
    session_absolute_ttl_seconds: int = 8 * 3600
    session_idle_ttl_seconds: int = 60 * 60
    login_max_attempts: int = 5
    login_lockout_seconds: int = 900
    session_cookie_secure: bool = True            # requires TLS at nginx
    
    # Bootstrap
    super_admin_username: str = ""
    super_admin_password: str = ""
    super_admin_ip: str = ""

    # --- Cost model ---
    llm_prices: dict = {}
    llm_price_currency: str = "USD"

    # LLM (Gemini via OpenAI-compatible API)
    # LLM_API_KEY may hold a single key OR a comma-separated list. Multiple keys
    # enable failover: when one Groq key hits its TPM/TPD ceiling the LLMService
    # rotates to the next (each Groq key carries an independent token budget).
    llm_api_key: str = ""
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    llm_model: str = "gemini-2.0-flash"
    # Provider transport: "openai" = any OpenAI-compatible endpoint (Groq, Ollama,
    # OpenAI, internal gateway); "azure" = Azure OpenAI (api-key header +
    # api-version query + deployment-based routing via AsyncAzureOpenAI). For
    # azure, LLM_BASE_URL is the resource root (https://<res>.openai.azure.com)
    # and LLM_MODEL is the *deployment* name (not the model family name).
    llm_provider: str = "openai"
    # api-version for the Azure LLM data-plane (chat completions). Newest models
    # (gpt-5 / o-series) require a recent preview version. Only used when
    # LLM_PROVIDER=azure.
    llm_azure_api_version: str = "2025-04-01-preview"
    # GPT-5 / o-series reasoning models reject `max_tokens` and require
    # `max_completion_tokens`. Set True for those Azure deployments. Default False
    # keeps the legacy `max_tokens` param for Groq/Ollama/OpenAI chat models.
    llm_use_max_completion_tokens: bool = False
    # Some reasoning models reject a non-default `temperature`. Set False to omit
    # the temperature param entirely. gpt-5.4 accepts it, so default True.
    llm_supports_temperature: bool = True
    # Reasoning models burn "reasoning tokens" before answering — the dominant
    # cost driver (high effort ≈ 5x the output tokens of low on the same task).
    # "minimal" | "low" | "medium" | "high"; empty = omit the param (provider
    # default) and is REQUIRED for non-reasoning models (Groq llama rejects it).
    llm_reasoning_effort: str = ""
    llm_insecure_tls: bool = False  # set True to bypass TLS verify (e.g. behind Cisco SSL inspection)
    llm_max_tokens: int = 4096      # hard cap on generated tokens per call (cost-leak guard)
    llm_request_timeout: float = 120.0  # per-request timeout in seconds (SDK-level)
    llm_max_retries: int = 2            # SDK-level retry count for transient errors

    # --- Chat-feature LLM override -------------------------------------------
    # The chat assistant (streaming Q&A) can run on a DIFFERENT provider than the
    # analysis/grading pipeline. Any CHAT_LLM_* left empty falls back to the main
    # LLM_* config above. Used to keep chat on Groq while analysis moves to Azure.
    chat_llm_provider: str = ""
    chat_llm_base_url: str = ""
    chat_llm_model: str = ""
    chat_llm_api_key: str = ""
    chat_llm_max_tokens: int = 0          # 0 = inherit llm_max_tokens
    chat_llm_insecure_tls: bool = False
    chat_llm_use_max_completion_tokens: bool = False
    chat_llm_supports_temperature: bool = True
    chat_llm_azure_api_version: str = ""  # empty = inherit llm_azure_api_version
    chat_llm_reasoning_effort: str = ""   # empty = omit (Groq llama rejects it)

    # --- Critic LLM override (independent generator/critic dual model) --------
    # The generator/critic loop runs the critic on a DIFFERENT model than the
    # analysis generator (e.g. generator gpt-5.4, critic gpt-5.4-nano). Any
    # CRITIC_LLM_* left empty falls back to the main LLM_* config above. In
    # practice only CRITIC_LLM_MODEL (the nano deployment name) is set.
    critic_llm_provider: str = ""
    critic_llm_base_url: str = ""
    critic_llm_model: str = ""
    critic_llm_api_key: str = ""
    critic_llm_max_tokens: int = 0          # 0 = inherit llm_max_tokens
    critic_llm_insecure_tls: bool = False
    # None = inherit the main LLM_* value. These MUST inherit (not default) so a
    # critic on the same Azure gpt-5.4 family picks up LLM_SUPPORTS_TEMPERATURE=
    # false / LLM_USE_MAX_COMPLETION_TOKENS=true. The validator below maps the
    # empty string that docker-compose forwards (${VAR:-}) to None — without it
    # pydantic raises on '' and the backend never boots.
    critic_llm_use_max_completion_tokens: Optional[bool] = None
    critic_llm_supports_temperature: Optional[bool] = None
    critic_llm_azure_api_version: str = ""  # empty = inherit llm_azure_api_version
    critic_llm_reasoning_effort: str = ""   # empty = omit

    @field_validator(
        "critic_llm_use_max_completion_tokens",
        "critic_llm_supports_temperature",
        mode="before",
    )
    @classmethod
    def _blank_critic_bool_means_inherit(cls, v):
        # docker-compose forwards these as ${VAR:-}; an unset host var arrives as
        # an empty string. Treat blank as None ("inherit main") rather than let
        # pydantic fail bool-parsing '' (which would crash Settings() at import).
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    # Hard daily token ceiling across ALL keys/models/endpoints (wallet guard).
    # 0 = disabled. When exceeded, every LLM entrypoint fails closed until UTC midnight.
    llm_global_daily_token_budget: int = 0
    # Context window of the deployed model — used for the pre-call token budget
    # check so the assembled prompt can't silently overflow and truncate the tail.
    llm_context_window: int = 128_000
    critic_enabled: bool = True
    # Independent on/off for the gpt-5.4-nano LLM critic on the precedent path.
    # Separate from critic_enabled, which gates the deterministic grounding check.
    llm_critic_enabled: bool = True

    @staticmethod
    def _parse_keys(raw: str) -> List[str]:
        """Parse a (possibly comma-separated) key string into an ordered,
        de-duplicated list. Duplicates collapse because the same key is the same
        upstream account/budget — rotating between identical keys buys nothing
        and would corrupt per-key rate-limit accounting."""
        seen = set()
        keys: List[str] = []
        for chunk in (raw or "").split(","):
            k = chunk.strip()
            if k and k not in seen:
                seen.add(k)
                keys.append(k)
        return keys

    @property
    def llm_api_keys(self) -> List[str]:
        """LLM_API_KEY parsed into an ordered, de-duplicated list of keys.

        A bare key (no comma) yields a one-element list, preserving prior
        single-key behaviour.
        """
        return self._parse_keys(self.llm_api_key)

    @property
    def chat_llm_api_keys(self) -> List[str]:
        """CHAT_LLM_API_KEY parsed; empty falls back to the main LLM keys so the
        chat feature inherits the analysis provider unless explicitly overridden."""
        keys = self._parse_keys(self.chat_llm_api_key)
        return keys or self.llm_api_keys

    @property
    def critic_llm_api_keys(self) -> List[str]:
        """CRITIC_LLM_API_KEY parsed; empty falls back to the main LLM keys so the
        critic inherits the analysis provider unless explicitly overridden."""
        keys = self._parse_keys(self.critic_llm_api_key)
        return keys or self.llm_api_keys

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
    # Trust the X-Forwarded-For header for the rate-limit / budget caller key.
    # Enable ONLY when the app sits behind a trusted reverse proxy / ingress
    # that sets XFF — when the app is directly internet-facing this header is
    # client-controlled and trivially spoofed, so leave it False there.
    trust_forwarded_for: bool = False

    # Chat input caps (prompt-bloat / cost guard). Bound how much client-supplied
    # text reaches the LLM: the latest message plus a tail of conversation history.
    chat_max_message_chars: int = 8000
    chat_max_history_messages: int = 20
    chat_max_history_chars: int = 4000   # per history message

    # File Upload
    max_upload_size: int = 52428800  # 50MB
    upload_dir: str = "./uploads"
    # An 'analyzing' submission whose latest AnalysisRun is still 'running'
    # past this many minutes with no finished_at is treated as orphaned (the
    # owning process was killed mid-run) and reclaimed instead of wedging the
    # submission on 'analyzing' forever with no recovery path.
    stale_analysis_run_minutes: int = 15
    # --- Pixel-faithful comparison rendering ---
    # Gotenberg sidecar (wraps LibreOffice) used to convert Word -> PDF before
    # rendering pages. Internal compose DNS name; no API key.
    gotenberg_url: str = "http://gotenberg:3000"
    # Max pages rendered per side; surplus is reported as truncated_pages, never
    # silently dropped.
    pixel_render_page_cap: int = 60
    # --- OCR fallback for scanned/image PDFs (no selectable text layer) ---
    # When a PDF yields no extractable text, rasterize its pages and OCR them so
    # the comparison still runs instead of failing. Needs the tesseract-ocr
    # binary (installed in the backend image) + pytesseract; degrades to a clear
    # "run OCR" message if either is missing. OCR is slow, so cap the pages.
    compare_ocr_enabled: bool = True
    compare_ocr_page_cap: int = 30
    # Root that knowledge-base ingest is confined to. Any folder_path outside
    # this tree is rejected (prevents arbitrary server-side file read — audit C8).
    kb_ingest_root: str = "./uploads"

    # LangSmith Tracing
    langchain_tracing_v2: str = "false"
    langchain_endpoint: str = "https://api.smith.langchain.com"
    langchain_api_key: str = ""
    langchain_project: str = "Regulatory Compliance Agent"

    # RAG — pluggable backend
    rag_embedding_provider: str = "azure_cohere"   # ONLY azure_cohere is supported
    rag_vector_backend: str = "pgvector"          # pgvector | azure_search
    rag_embedding_model: str = "Cohere-embed-v3-multilingual"
    rag_embedding_dim: int = 1024
    rag_top_k_analysis: int = 8
    rag_top_k_chat: int = 5
    rag_top_k_similar: int = 3
    rag_score_threshold: float = 0.0              # 0.0 = no floor; RRF scores are unbounded-low
    # Minimum cosine similarity on the VECTOR leg before fusion. Unlike RRF
    # (unbounded), cosine is in [-1,1], so this is a meaningful relevance floor.
    # Candidates below it are dropped, so an unrelated chunk can legitimately
    # retrieve zero precedents → knowledge_base_empty → fail closed (audit C6).
    rag_min_cosine: float = 0.25
    # Minimum BM25 ts_rank_cd on the KEYWORD leg before fusion. Without it, a row
    # sharing a single common token ("policy", "premium") surfaces into RRF with
    # no relevance guarantee and can be cited (manufactured findings). cosine
    # gates the vector leg; this gates the keyword leg. Tune against the eval set.
    rag_min_ts_rank: float = 0.02
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

    # Azure AI Foundry — model-inference endpoint. Cohere models (embed v3,
    # rerank v4) deployed in the Foundry project are NOT served on the Azure
    # OpenAI surface (.openai.azure.com); they are reached via the
    # azure-ai-inference SDK at https://<resource>.services.ai.azure.com/models
    # with an api-version query + key credential. Used by
    # RAG_EMBEDDING_PROVIDER=azure_cohere. If AZURE_INFERENCE_API_KEY is empty
    # the embedder falls back to the first LLM_API_KEY (same Foundry resource).
    azure_inference_endpoint: str = ""              # AZURE_INFERENCE_ENDPOINT
    azure_inference_api_key: str = ""               # AZURE_INFERENCE_API_KEY
    azure_inference_api_version: str = "2024-05-01-preview"
    azure_cohere_embed_deployment: str = "Cohere-embed-v3-multilingual"   # AZURE_COHERE_EMBED_DEPLOYMENT
    # Reserved: rerank is not wired into the pgvector retrieval path yet.
    azure_cohere_rerank_deployment: str = ""        # AZURE_COHERE_RERANK_DEPLOYMENT
    # Per-text embedding cache (Priority 4d) — avoids re-embedding repeated chunks.
    embed_cache_size: int = 2048

    # Azure AI Search (v2, vector store)
    azure_search_endpoint: str = ""
    azure_search_api_key: str = ""
    azure_search_rules_index: str = "rag-rules"
    azure_search_chunks_index: str = "rag-chunks"
    azure_search_source_docs_index: str = "rag-source-docs"

    # Product-doc grounding (2026-06-22) — fact cards (deterministic) + brochure
    # passages (semantic) injected into the analysis prompt. Additive: a no-match
    # leaves precedent/rule/novel grading unchanged.
    product_grounding_enabled: bool = True
    product_fact_cards_dir: str = "data/product_fact_cards"
    # Mandatory-Disclosure Checker (2026-06-26). Gates disclosure_node.
    disclosure_check_enabled: bool = True
    disclaimers_dir: str = "data/disclaimers"
    # LLM backstop recovers paraphrased obligations the regex misses; when off,
    # only deterministic (product-line + keyword) triggers fire.
    disclosure_llm_backstop_enabled: bool = True
    # The backstop reads the WHOLE document in overlapping windows. It used to be
    # handed the first 6,000 chars only, so paraphrased obligations in the tail of
    # a long brochure were invisible. Window size barely moves token cost (the same
    # document tokens are sent either way) â€” it mainly sets the request count, so
    # keep it large enough that a typical document is a handful of calls.
    disclosure_llm_window_chars: int = 20_000
    # Overlap so an obligation phrase straddling a boundary appears intact in the
    # next window. MUST stay below the window size or windowing cannot advance.
    disclosure_llm_window_overlap_chars: int = 500
    # Windows are independent; this many run concurrently per batch, so wall-clock
    # stays close to one round-trip instead of N.
    disclosure_llm_max_concurrency: int = 4
    product_docs_top_k: int = 3          # brochure passages per chunk (Path B)
    product_match_max: int = 3           # max products grounded per document

    # Max chunks graded concurrently. The ceiling exists because a 429 fails
    # the chunk, which closes the whole run at "needs_review" — throughput is
    # bounded by the provider's per-minute token limit, not by CPU.
    #
    # Was 2, calibrated for the Groq free tier. Production now runs a dedicated
    # Azure deployment with its own TPM quota, where 2 left the pipeline idle:
    # a measured run spent ~5.5 min on one brochure at ~3 LLM calls per chunk
    # (grade + completeness sweep + critic) two chunks at a time. Raised to 6.
    # If 429s appear, lower it — do NOT raise this to chase speed without
    # checking the deployment's TPM headroom first, since the failure mode is a
    # lost run rather than a slow one.
    grade_concurrency: int = 6

    # Completeness sweep (recall fix 2026-06-08): run a second per-chunk "what did
    # you miss?" grading pass and merge the additional findings. A single
    # structured pass systematically under-enumerates on dense copy. This DOUBLES
    # LLM calls per chunk — disable on a tight Groq free-tier quota. Override via env.
    completeness_sweep_enabled: bool = True

    # Cross-chunk context: grade each chunk against a read-only view of the whole
    # document so a disclaimer/reference present elsewhere (e.g. footer) isn't
    # falsely flagged as missing. Token budget caps the full-document mode; over
    # budget falls back to a window that always keeps the footer. See
    # docs/superpowers/specs/2026-06-15-cross-chunk-context-design.md.
    cross_chunk_context_enabled: bool = True
    cross_chunk_context_token_budget: int = 8000

    # Scoring policy version — stamped onto every AnalysisRun at open_run time
    # (analysis_runs.scoring_policy_version, migration 0029) so a later change
    # to the scoring formula/weights can't silently make historic run scores
    # incomparable without a record of which policy produced them.
    scoring_policy_version: str = "1.0"

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"
        env_prefix = ""


settings = Settings()
