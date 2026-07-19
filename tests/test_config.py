import logging
from pathlib import Path

import pytest

from core.config import Settings
from db.catalog import _document_values


def test_settings_create_directories(tmp_path: Path):
    settings = Settings(temp_processing_dir=tmp_path / "processing")
    assert settings.temp_processing_dir.is_dir()
    assert settings.embedding_dimension == 1536
    assert settings.max_media_context_size_bytes == 4 * 1024 * 1024


def test_invalid_overlap_is_rejected():
    with pytest.raises(ValueError):
        Settings(chunk_size=100, chunk_overlap=100)


def test_invalid_top_k_is_rejected():
    with pytest.raises(ValueError):
        Settings(default_top_k=10, max_top_k=5)


def test_media_budget_must_be_below_upload_limit():
    with pytest.raises(ValueError):
        Settings(max_upload_size_mb=60, max_media_context_size_mb=60)


def test_development_keeps_safe_local_defaults(tmp_path: Path):
    settings = Settings(_env_file=None, app_env="development", database_url="", temp_processing_dir=tmp_path / "processing")
    assert settings.database_url == ""
    assert settings.temp_processing_dir.is_dir()
    assert settings.r2_region == "auto"
    assert settings.public_demo_mode is False


def test_zero_retention_disables_expiry_and_positive_retention_fills_it(tmp_path: Path):
    record = {
        "doc_id": "doc-1",
        "original_name": "notes.txt",
        "sanitized_name": "notes.txt",
        "object_key": "rag/test/default/documents/doc-1/original/notes.txt",
        "file_type": "text",
        "mime_type": "text/plain",
        "sha256": "a" * 64,
        "size_bytes": 1,
    }
    zero = Settings(_env_file=None, app_env="test", public_retention_days=0, temp_processing_dir=tmp_path / "p0")
    positive = Settings(_env_file=None, app_env="test", public_retention_days=7, temp_processing_dir=tmp_path / "p1")
    assert zero.public_retention_days == 0
    assert _document_values(record, retention_days=0)[0].get("expires_at") is None
    assert _document_values(record, retention_days=positive.public_retention_days)[0]["expires_at"] is not None


def test_production_reports_missing_required_settings():
    with pytest.raises(ValueError) as error:
        Settings(_env_file=None, app_env="production", database_url="")
    message = str(error.value)
    assert "DATABASE_URL" in message
    assert "R2_ACCESS_KEY_ID" in message
    assert "R2_SECRET_ACCESS_KEY" in message
    assert "R2_BUCKET_NAME" in message
    assert "ADMIN_TOKEN" in message
    assert "VISITOR_SESSION_SECRET" in message


def test_production_demo_requires_rate_limit_secret(tmp_path: Path):
    with pytest.raises(ValueError, match="RATE_LIMIT_SECRET") as error:
        Settings(_env_file=None, app_env="production", database_url="postgresql+asyncpg://user:password@host/db", r2_access_key_id="access", r2_secret_access_key="secret", r2_bucket_name="bucket", admin_token="admin", visitor_session_secret="visitor-session-secret-at-least-32", public_demo_mode=True, temp_processing_dir=tmp_path / "processing", r2_endpoint_url="https://r2.example")
    assert "password" not in str(error.value)
    assert "secret" not in str(error.value)


def test_secrets_are_absent_from_settings_repr_and_logs(caplog):
    settings = Settings(_env_file=None, google_api_key="google-secret", pinecone_api_key="pinecone-secret", database_url="postgresql+asyncpg://user:db-secret@host/db", r2_access_key_id="r2-access-secret", r2_secret_access_key="r2-secret", admin_token="admin-secret", rate_limit_secret="rate-secret", visitor_session_secret="visitor-secret")
    caplog.set_level(logging.INFO)
    logging.getLogger("tests.config").info("settings=%r", settings)
    rendered = f"{settings!r} {settings!s} {caplog.text}"
    for secret in ("google-secret", "pinecone-secret", "db-secret", "r2-access-secret", "r2-secret", "admin-secret", "rate-secret", "visitor-secret"):
        assert secret not in rendered
