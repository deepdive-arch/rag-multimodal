"""Centralized, validated application settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment and .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    frontend_origin: str = "http://localhost:3000"
    google_api_key: str = ""
    pinecone_api_key: str = ""
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
    max_chat_history_messages: int = 6
    max_upload_size_mb: int = 100
    max_pdf_pages: int = 200
    max_audio_duration_seconds: int = 180
    max_video_duration_seconds: int = 120
    uploads_dir: Path = Path(".tmp/uploads")
    derived_dir: Path = Path(".tmp/uploads/derived")
    database_path: Path = Path(".tmp/rag.db")
    admin_token: str = ""
    log_level: str = "INFO"

    @property
    def max_upload_size_bytes(self) -> int:
        """Return the upload limit in bytes."""
        return self.max_upload_size_mb * 1024 * 1024

    @model_validator(mode="after")
    def validate_and_prepare(self) -> "Settings":
        """Validate cross-field constraints and prepare local directories."""
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        if self.embedding_dimension <= 0:
            raise ValueError("EMBEDDING_DIMENSION must be positive")
        if self.max_upload_size_mb <= 0 or self.max_pdf_pages <= 0:
            raise ValueError("upload and PDF limits must be positive")
        if self.max_top_k < self.default_top_k:
            raise ValueError("MAX_TOP_K must be greater than or equal to DEFAULT_TOP_K")
        if self.min_relevance_score < -1 or self.min_relevance_score > 1:
            raise ValueError("MIN_RELEVANCE_SCORE must be between -1 and 1")
        if self.app_env.lower() == "production" and not self.admin_token:
            raise ValueError("ADMIN_TOKEN is required in production")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.derived_dir.mkdir(parents=True, exist_ok=True)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
