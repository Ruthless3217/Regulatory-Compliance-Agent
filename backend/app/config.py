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

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"
        env_prefix = ""


settings = Settings()
