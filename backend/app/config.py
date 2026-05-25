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
    llm_api_key: str = ""
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    llm_model: str = "gemini-2.0-flash"
    llm_insecure_tls: bool = False  # set True to bypass TLS verify (e.g. behind Cisco SSL inspection)

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

    # File Upload
    max_upload_size: int = 52428800  # 50MB
    upload_dir: str = "./uploads"

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
    rag_recall_pool: int = 30
    rag_rrf_k: int = 60
    rag_active_categories: List[str] = ["regulatory", "brand", "seo", "irdai", "sebi"]

    # Precedent compliance engine (Phase 1)
    pgvector_top_k: int = 5                       # precedents retrieved per chunk
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
