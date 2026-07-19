import asyncio
import os
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from core.config import Settings
from core.exceptions import CapacityExceededError, QuotaExceededError
from db.catalog import Catalog


pytestmark = pytest.mark.integration


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "test",
        "database_url": os.environ["TEST_DATABASE_URL"],
        "public_demo_mode": True,
        "rate_limit_secret": "integration-only-secret",
        "max_daily_uploads_per_client": 3,
        "max_daily_queries_per_client": 3,
        "max_total_stored_bytes": 100,
        "max_active_documents": 20,
        "temp_processing_dir": tmp_path / "processing",
    }
    return Settings(**(values | overrides))


def _record(index: int, size: int = 10) -> dict:
    digest = f"{index:064x}"
    return {
        "doc_id": str(uuid4()),
        "original_name": f"document-{index}.txt",
        "sanitized_name": f"document-{index}.txt",
        "object_key": f"uploads/{digest}.txt",
        "file_type": "text",
        "mime_type": "text/plain",
        "sha256": digest,
        "size_bytes": size,
        "status": "pending_upload",
    }


@pytest.mark.asyncio
async def test_concurrent_presign_reservations_are_atomic_and_persisted(test_schema, tmp_path):
    settings = _settings(tmp_path, max_daily_uploads_per_client=2)
    catalog = Catalog.ephemeral(settings)
    usage_date = date(2026, 7, 18)
    try:
        await catalog.clear_catalog()

        async def reserve(index):
            try:
                await catalog.create_document_with_quota(_record(index, 7), "hmac-client", usage_date)
                return "accepted"
            except QuotaExceededError:
                return "quota"

        outcomes = await asyncio.gather(*(reserve(index) for index in range(4)))
        assert outcomes.count("accepted") == 2
        assert outcomes.count("quota") == 2
        usage = await catalog.get_usage("hmac-client", usage_date)
        assert usage["uploads_count"] == 2
        assert usage["bytes_uploaded"] == 14
    finally:
        await catalog.clear_catalog()
        await catalog.close()


@pytest.mark.asyncio
async def test_query_quota_persists_across_backend_catalog_restarts(test_schema, tmp_path):
    settings = _settings(tmp_path, max_daily_queries_per_client=2)
    usage_date = date(2026, 7, 18)
    first = Catalog.ephemeral(settings)
    await first.clear_catalog()
    await first.reserve_query_quota("hmac-client", usage_date)
    await first.close()
    restarted = Catalog.ephemeral(settings)
    await restarted.reserve_query_quota("hmac-client", usage_date)
    await restarted.close()
    final = Catalog.ephemeral(settings)
    try:
        with pytest.raises(QuotaExceededError):
            await final.reserve_query_quota("hmac-client", usage_date)
        assert (await final.get_usage("hmac-client", usage_date))["queries_count"] == 2
    finally:
        await final.clear_catalog()
        await final.close()


@pytest.mark.asyncio
async def test_daily_quota_rotates_and_global_capacity_is_enforced(test_schema, tmp_path):
    settings = _settings(tmp_path, max_daily_uploads_per_client=1, max_total_stored_bytes=15, max_active_documents=10)
    catalog = Catalog.ephemeral(settings)
    first_day = date(2026, 7, 18)
    second_day = first_day + timedelta(days=1)
    try:
        await catalog.clear_catalog()
        await catalog.create_document_with_quota(_record(10, 10), "hmac-client", first_day)
        with pytest.raises(QuotaExceededError):
            await catalog.create_document_with_quota(_record(11, 1), "hmac-client", first_day)
        await catalog.create_document_with_quota(_record(12, 5), "hmac-client", second_day)
        with pytest.raises(CapacityExceededError):
            await catalog.create_document_with_quota(_record(13, 1), "other-client", second_day)
        assert (await catalog.get_usage("hmac-client", second_day))["uploads_count"] == 1
    finally:
        await catalog.clear_catalog()
        await catalog.close()
