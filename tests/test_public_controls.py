from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from api.server import app
from core.config import Settings
from services.abuse import hash_client_address, retry_after_until_next_utc_day, utc_usage_date


def test_client_identity_is_hmac_and_never_the_plain_address():
    address = "203.0.113.8"
    digest = hash_client_address(address, "test-secret")
    assert len(digest) == 64
    assert digest != address
    assert digest == hash_client_address(address, "test-secret")
    assert digest != hash_client_address(address, "rotated-secret")


def test_usage_period_rotates_by_utc_date():
    first = datetime(2026, 7, 18, 23, 59, tzinfo=UTC)
    second = first + timedelta(minutes=2)
    assert utc_usage_date(first) != utc_usage_date(second)
    assert retry_after_until_next_utc_day(first) == 60


def test_cors_origins_are_exact_and_development_only_localhost(tmp_path: Path):
    development = Settings(
        _env_file=None,
        app_env="development",
        frontend_origin="https://demo.example",
        temp_processing_dir=tmp_path / "dev-processing",
    )
    production = Settings(
        _env_file=None,
        app_env="production",
        frontend_origin="https://demo.example",
        database_url="postgresql+asyncpg://user:password@host/db",
        r2_endpoint_url="https://account.r2.cloudflarestorage.com",
        r2_access_key_id="access",
        r2_secret_access_key="secret",
        r2_bucket_name="bucket",
        admin_token="admin",
        rate_limit_secret="server-only-secret",
        visitor_session_secret="visitor-session-secret-at-least-32",
        temp_processing_dir=tmp_path / "prod-processing",
    )
    assert "http://localhost:3000" in development.cors_origins
    assert production.cors_origins == ["https://demo.example"]
    assert "*" not in production.cors_origins


def test_public_demo_health_contains_only_safe_limits(monkeypatch, tmp_path: Path):
    settings = Settings(
        _env_file=None,
        app_env="test",
        public_demo_mode=True,
        rate_limit_secret="server-only-secret",
        temp_processing_dir=tmp_path / "processing",
    )
    monkeypatch.setattr("api.server.get_settings", lambda: settings)
    monkeypatch.setattr("api.server.Catalog.is_ready", lambda self: _ready())
    monkeypatch.setattr("api.server.index_health", lambda _: "missing_key")
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["public_demo"]["enabled"] is True
    assert settings.rate_limit_secret not in response.text


def test_frontend_public_surface_has_no_admin_secret_or_global_destructive_controls():
    root = Path(__file__).resolve().parents[1] / "frontend"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("**/*") if path.is_file() and path.suffix in {".ts", ".tsx", ".css", ".json", ".mjs"} and "node_modules" not in path.parts and "out" not in path.parts)
    assert "ADMIN_TOKEN" not in source
    assert "X-Admin-Token" not in source
    assert "onClearIndex" not in source
    assert "DELETE_ALL" not in source
    assert '"/api/index"' not in source
    assert "deleteFile" in source
    assert "Ambiente público de demonstração" in source


async def _ready():
    return True
