from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Knowledge Intelligence Engine"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://knowledge:knowledge@localhost:5432/knowledge"
    storage_dir: Path = Path("./data/uploads")
    max_upload_bytes: int = 50 * 1024 * 1024
    auto_create_schema: bool = False
    default_tenant_id: str = "default"
    embedding_dimension: int = 384
    openai_api_key: str | None = None
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    llama_cloud_api_key: SecretStr | None = None
    llama_parse_enabled: bool = True
    # Retained for environment compatibility; strict ingestion never uses local fallback.
    llama_parse_fallback_local: bool = False
    llama_parse_tier: str = "agentic"
    llama_parse_version: str = "latest"
    llama_parse_ocr_languages: list[str] = Field(default_factory=lambda: ["en"])
    llama_parse_request_timeout_seconds: float = 300.0
    llama_parse_job_timeout_seconds: int = 900
    llama_parse_max_wait_seconds: int = 1800
    llama_parse_poll_interval_seconds: float = 5.0
    llama_parse_max_retries: int = 3
    llama_parse_retry_backoff_seconds: float = 3.0
    admin_username: str = "admin"
    admin_password: SecretStr = SecretStr("admin")
    auth_token_ttl_seconds: int = 8 * 60 * 60
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return settings
