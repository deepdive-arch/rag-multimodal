"""Centralized, validated application settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment and .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, hide_input_in_errors=True, extra="ignore")

    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    frontend_origin: str = "http://localhost:3000"
    google_api_key: str = Field(default="", repr=False)
    pinecone_api_key: str = Field(default="", repr=False)
    pinecone_index_name: str = "rag-multimodal"
    pinecone_namespace: str = "default"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"
    gemini_embedding_model: str = "gemini-embedding-2"
    gemini_generation_model: str = "gemini-3.5-flash"
    embedding_dimension: int = 1536
    chunk_size: int = 1500
    chunk_overlap: int = 250
    min_chunk_size: int = 100
    text_preview_size: int = 400
    default_top_k: int = 5
    max_top_k: int = 20
    min_relevance_score: float = 0.35
    max_matches_per_document: int = 3
    max_media_parts_per_query: int = 3
    max_media_context_size_mb: int = 4
    max_chat_history_messages: int = 6
    max_upload_size_mb: int = 10
    max_pdf_pages: int = 20
    max_pdf_page_pixels: int = 20_000_000
    max_image_pixels: int = 40_000_000
    max_audio_duration_seconds: int = 60
    max_video_duration_seconds: int = 60
    database_url: str = Field(default="", repr=False)
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_timeout_seconds: int = 30
    database_connect_timeout_seconds: int = 10
    database_health_timeout_seconds: float = 2.0
    r2_account_id: str = ""
    r2_access_key_id: str = Field(default="", repr=False)
    r2_secret_access_key: str = Field(default="", repr=False)
    r2_bucket_name: str = ""
    r2_endpoint_url: str = ""
    r2_region: str = "auto"
    r2_object_prefix: str = "rag"
    r2_presigned_url_ttl_seconds: int = 600
    r2_presigned_upload_ttl_seconds: int = 300
    r2_upload_version: str = "1"
    r2_connect_timeout_seconds: int = 5
    r2_read_timeout_seconds: int = 30
    r2_max_attempts: int = 3
    r2_health_cache_seconds: float = 15.0
    temp_processing_dir: Path = Path(".tmp/processing")
    public_demo_mode: bool = False
    visitor_session_secret: str = Field(default="", repr=False)
    visitor_cookie_samesite: str = ""
    visitor_cookie_max_age_seconds: int = 60 * 60 * 24 * 365
    public_retention_days: int = 3
    cleanup_batch_size: int = 2
    cleanup_timeout_seconds: float = 10.0
    deletion_lease_seconds: int = 120
    max_daily_uploads_per_client: int = 3
    max_daily_queries_per_client: int = 30
    max_total_stored_bytes: int = 1024 * 1024 * 1024
    max_active_documents: int = 50
    rate_limit_secret: str = Field(default="", repr=False)
    admin_token: str = Field(default="", repr=False)
    log_level: str = "INFO"

    @property
    def max_upload_size_bytes(self) -> int:
        """Return the upload limit in bytes."""
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def max_media_context_size_bytes(self) -> int:
        """Return the aggregate inline media budget in bytes."""
        return self.max_media_context_size_mb * 1024 * 1024

    @property
    def cors_origins(self) -> list[str]:
        """Return the exact browser origins allowed by this environment."""
        origins = {self.frontend_origin.rstrip("/")}
        if self.app_env.lower() == "development":
            origins.update({"http://localhost:3000", "http://127.0.0.1:3000"})
        return sorted(origin for origin in origins if origin)

    @property
    def visitor_cookie_same_site(self) -> str:
        """Use cross-site cookies in production when frontend and API are split."""
        return self.visitor_cookie_samesite.lower() or ("none" if self.app_env.lower() == "production" else "lax")

    @property
    def visitor_cookie_secret(self) -> str:
        """Return the explicit production secret or an isolated local-only key."""
        return self.visitor_session_secret or "local-test-only-visitor-session-secret"

    @model_validator(mode="after")
    def validate_and_prepare(self) -> "Settings":
        """Validate cross-field constraints and prepare local directories."""
        environment = self.app_env.lower()
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        if self.embedding_dimension <= 0:
            raise ValueError("EMBEDDING_DIMENSION must be positive")
        if self.max_upload_size_mb <= 0 or self.max_pdf_pages <= 0 or self.max_pdf_page_pixels <= 0 or self.max_image_pixels <= 0 or self.max_audio_duration_seconds <= 0 or self.max_video_duration_seconds <= 0:
            raise ValueError("upload, PDF and media duration limits must be positive")
        if self.max_media_context_size_mb <= 0 or self.max_media_context_size_mb >= self.max_upload_size_mb:
            raise ValueError("MAX_MEDIA_CONTEXT_SIZE_MB must be positive and below MAX_UPLOAD_SIZE_MB")
        if self.max_media_parts_per_query <= 0:
            raise ValueError("MAX_MEDIA_PARTS_PER_QUERY must be positive")
        if self.max_top_k < self.default_top_k:
            raise ValueError("MAX_TOP_K must be greater than or equal to DEFAULT_TOP_K")
        if self.min_relevance_score < -1 or self.min_relevance_score > 1:
            raise ValueError("MIN_RELEVANCE_SCORE must be between -1 and 1")
        if self.database_pool_size <= 0 or self.database_max_overflow < 0:
            raise ValueError("DATABASE_POOL_SIZE must be positive and DATABASE_MAX_OVERFLOW cannot be negative")
        if self.database_pool_timeout_seconds <= 0 or self.database_connect_timeout_seconds <= 0 or self.database_health_timeout_seconds <= 0:
            raise ValueError("database timeouts must be positive")
        if self.database_url and not self.database_url.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
            raise ValueError("DATABASE_URL must be a PostgreSQL connection string")
        if self.r2_presigned_url_ttl_seconds <= 0:
            raise ValueError("R2_PRESIGNED_URL_TTL_SECONDS must be positive")
        if self.r2_presigned_url_ttl_seconds > 3600:
            raise ValueError("R2_PRESIGNED_URL_TTL_SECONDS cannot exceed one hour")
        if self.r2_presigned_upload_ttl_seconds < 30 or self.r2_presigned_upload_ttl_seconds > 900:
            raise ValueError("R2_PRESIGNED_UPLOAD_TTL_SECONDS must be between 30 and 900 seconds")
        if self.r2_connect_timeout_seconds <= 0 or self.r2_read_timeout_seconds <= 0:
            raise ValueError("R2 timeouts must be positive")
        if self.r2_max_attempts <= 0 or self.r2_max_attempts > 5:
            raise ValueError("R2_MAX_ATTEMPTS must be between 1 and 5")
        if self.r2_health_cache_seconds <= 0:
            raise ValueError("R2_HEALTH_CACHE_SECONDS must be positive")
        if self.public_retention_days < 0:
            raise ValueError("PUBLIC_RETENTION_DAYS cannot be negative")
        if self.visitor_cookie_same_site not in {"lax", "strict", "none"}:
            raise ValueError("VISITOR_COOKIE_SAMESITE must be lax, strict or none")
        if self.visitor_cookie_max_age_seconds <= 0:
            raise ValueError("VISITOR_COOKIE_MAX_AGE_SECONDS must be positive")
        if self.frontend_origin.strip() == "*":
            raise ValueError("FRONTEND_ORIGIN cannot be wildcard")
        if any(value <= 0 for value in (self.max_daily_uploads_per_client, self.max_daily_queries_per_client, self.max_total_stored_bytes, self.max_active_documents)):
            raise ValueError("public usage and storage limits must be positive")
        if self.cleanup_batch_size <= 0 or self.deletion_lease_seconds <= 0 or self.cleanup_timeout_seconds <= 0:
            raise ValueError("cleanup settings must be positive")
        if not self.r2_endpoint_url and self.r2_account_id:
            self.r2_endpoint_url = f"https://{self.r2_account_id}.r2.cloudflarestorage.com"
        missing = self._missing_production_settings(environment)
        if missing:
            raise ValueError(f"Production settings missing: {', '.join(missing)}")
        if environment == "production" and len(self.rate_limit_secret.strip()) < 16:
            raise ValueError("RATE_LIMIT_SECRET with at least 16 characters is required")
        if environment == "production" and len(self.visitor_session_secret.strip()) < 32:
            raise ValueError("VISITOR_SESSION_SECRET with at least 32 characters is required")
        if environment == "production" and any(host in self.frontend_origin.lower() for host in ("localhost", "127.0.0.1")):
            raise ValueError("FRONTEND_ORIGIN must be the real frontend URL in production")
        if environment == "production" and not self.r2_endpoint_url.lower().startswith("https://"):
            raise ValueError("R2_ENDPOINT_URL must use HTTPS in production")
        if environment == "production" and self.visitor_cookie_same_site == "none" and not self.frontend_origin.lower().startswith("https://"):
            raise ValueError("cross-site visitor cookies require an HTTPS FRONTEND_ORIGIN in production")
        self.r2_object_prefix = self.r2_object_prefix.strip("/")
        self.temp_processing_dir.mkdir(parents=True, exist_ok=True)
        return self

    def _missing_production_settings(self, environment: str) -> list[str]:
        """Return names of production settings that must be explicit."""
        if environment != "production":
            return []
        required = [("DATABASE_URL", self.database_url), ("R2_ACCESS_KEY_ID", self.r2_access_key_id), ("R2_SECRET_ACCESS_KEY", self.r2_secret_access_key), ("R2_BUCKET_NAME", self.r2_bucket_name), ("R2_ENDPOINT_URL", self.r2_endpoint_url), ("ADMIN_TOKEN", self.admin_token), ("RATE_LIMIT_SECRET", self.rate_limit_secret), ("VISITOR_SESSION_SECRET", self.visitor_session_secret)]
        return [name for name, value in required if not value.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
