import asyncio
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from core.config import Settings
from core.exceptions import DeletionError, ObjectStorageError
from core.visitor import new_visitor_id
from services import deletion
from services.storage import ObjectMetadata, document_object_prefix, object_storage_base_prefix


class FakeCatalog:
    def __init__(self, *, status="ready"):
        self.status = status
        self.stage = None
        self.error = None
        self.chunks = ["doc-1-chunk-0"]
        self.object_keys = []
        self.fail_postgres_once = False
        self.events = []

    async def begin_deletion(self, _doc_id, _lease_seconds):
        if self.status == "deleted":
            return {"status": "deleted", "claimed": False}
        if self.status == "deleting" and not self.error:
            return {"status": "deleting", "claimed": False, "stage": self.stage}
        self.status = "deleting"
        self.stage = self.stage or "pinecone"
        self.error = None
        return {
            "status": "deleting",
            "claimed": True,
            "stage": self.stage,
            "chunk_ids": list(self.chunks),
            "object_keys": list(self.object_keys),
        }

    async def mark_deletion_stage(self, _doc_id, stage, *, error=None, lease_seconds=120):
        if stage == "postgres" and self.fail_postgres_once and error is None:
            self.fail_postgres_once = False
            raise RuntimeError("database unavailable")
        self.stage = stage
        self.error = error
        self.events.append((stage, error))

    async def complete_deletion(self, _doc_id):
        self.status = "deleted"
        self.stage = "completed"

    async def close(self):
        return None

    async def get_file(self, _doc_id):
        return {"doc_id": "doc-1", "status": self.status, "deletion_stage": self.stage}

    async def record_audit_event(self, event_type, _details):
        self.events.append((event_type, None))

    async def clear_catalog(self):
        return None


class FakeStorage:
    def __init__(self, keys, *, fail=False):
        self.keys = set(keys)
        self.fail = fail
        self.deleted_calls = []

    async def list_objects_by_prefix(self, prefix):
        return [ObjectMetadata(key, 1, "application/octet-stream") for key in sorted(self.keys) if key.startswith(prefix)]

    async def delete_objects(self, keys):
        self.deleted_calls.append(list(keys))
        if self.fail:
            raise ObjectStorageError("R2 indisponível")
        self.keys.difference_update(keys)

    async def head_object(self, key):
        return ObjectMetadata(key, 1, "application/octet-stream") if key in self.keys else None


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="",
        temp_processing_dir=tmp_path / "processing",
        **overrides,
    )


@pytest.mark.asyncio
async def test_pinecone_success_r2_failure_keeps_deleting_and_retry_resumes(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    catalog = FakeCatalog()
    key = f"{document_object_prefix('doc-1', settings)}/original/a.txt"
    storage = FakeStorage([key], fail=True)
    deleted_vectors = []
    monkeypatch.setattr(deletion.Catalog, "ephemeral", lambda _settings: catalog)
    monkeypatch.setattr(deletion, "get_object_storage", lambda _settings: storage)
    monkeypatch.setattr(deletion, "delete_vectors", lambda ids, _settings: deleted_vectors.extend(ids))
    monkeypatch.setattr(deletion, "confirm_vectors_deleted", lambda *_args: None)

    with pytest.raises(DeletionError):
        await deletion.delete_document_async("doc-1", settings)

    assert catalog.status == "deleting"
    assert catalog.stage == "r2"
    assert catalog.error
    assert deleted_vectors == ["doc-1-chunk-0"]

    storage.fail = False
    outcome = await deletion.delete_document_async("doc-1", settings)
    assert outcome.status == "deleted"
    assert catalog.status == "deleted"
    assert len(deleted_vectors) == 1
    assert not storage.keys


@pytest.mark.asyncio
async def test_r2_success_postgres_failure_is_retryable_without_external_redelete(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    catalog = FakeCatalog()
    key = f"{document_object_prefix('doc-1', settings)}/derived/page.png"
    catalog.object_keys = [key]
    storage = FakeStorage([key])
    vector_calls = []
    catalog.fail_postgres_once = True
    monkeypatch.setattr(deletion.Catalog, "ephemeral", lambda _settings: catalog)
    monkeypatch.setattr(deletion, "get_object_storage", lambda _settings: storage)
    monkeypatch.setattr(deletion, "delete_vectors", lambda ids, _settings: vector_calls.append(ids))
    monkeypatch.setattr(deletion, "confirm_vectors_deleted", lambda *_args: None)

    with pytest.raises(DeletionError):
        await deletion.delete_document_async("doc-1", settings)

    assert catalog.status == "deleting"
    assert catalog.stage == "postgres"
    assert not storage.keys

    outcome = await deletion.delete_document_async("doc-1", settings)
    assert outcome.status == "deleted"
    assert len(vector_calls) == 1
    assert len(storage.deleted_calls) == 1


@pytest.mark.asyncio
async def test_two_simultaneous_deletions_only_one_worker_claims(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    catalog = FakeCatalog()
    storage = FakeStorage([])
    started = threading.Event()
    release = threading.Event()

    def delete_vectors(_ids, _settings):
        started.set()
        release.wait(2)

    monkeypatch.setattr(deletion.Catalog, "ephemeral", lambda _settings: catalog)
    monkeypatch.setattr(deletion, "get_object_storage", lambda _settings: storage)
    monkeypatch.setattr(deletion, "delete_vectors", delete_vectors)
    monkeypatch.setattr(deletion, "confirm_vectors_deleted", lambda *_args: None)

    first = asyncio.create_task(deletion.delete_document_async("doc-1", settings))
    await asyncio.to_thread(started.wait, 1)
    second = await deletion.delete_document_async("doc-1", settings)
    release.set()
    first_result = await first

    assert first_result.status == "deleted"
    assert second.status == "deleting"
    assert second.claimed is False


@pytest.mark.asyncio
async def test_deleted_document_is_idempotent_without_external_calls(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    catalog = FakeCatalog(status="deleted")
    called = []
    monkeypatch.setattr(deletion.Catalog, "ephemeral", lambda _settings: catalog)
    monkeypatch.setattr(deletion, "delete_vectors", lambda *_args: called.append("pinecone"))
    outcome = await deletion.delete_document_async("doc-1", settings)
    assert outcome.status == "deleted"
    assert called == []


@pytest.mark.asyncio
async def test_namespace_cleanup_uses_configured_scope_and_never_deletes_index(monkeypatch, tmp_path):
    settings = _settings(tmp_path, admin_token="secret", pinecone_namespace="test-env")
    catalog = FakeCatalog()
    base = object_storage_base_prefix(settings)
    scoped = f"{base}/doc-1/original/a.txt"
    storage = FakeStorage([scoped])
    calls = []
    monkeypatch.setattr(deletion.Catalog, "ephemeral", lambda _settings: catalog)
    monkeypatch.setattr(deletion, "get_object_storage", lambda _settings: storage)
    monkeypatch.setattr(deletion, "delete_all_vectors", lambda _settings: calls.append("delete-namespace"))
    monkeypatch.setattr(deletion, "confirm_namespace_empty", lambda _settings: calls.append("confirm-namespace"))

    result = await deletion.clear_namespace_async(settings, "DELETE_ALL", "secret")
    assert result["namespace"] == "test-env"
    assert calls == ["delete-namespace", "confirm-namespace"]
    assert not storage.keys


@pytest.mark.asyncio
async def test_r2_registered_key_injection_is_rejected_before_delete(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    owner, victim = new_visitor_id(), new_visitor_id()
    victim_key = f"{document_object_prefix('doc-1', settings, victim)}/original/a.txt"
    storage = FakeStorage([victim_key])
    monkeypatch.setattr(deletion, "get_object_storage", lambda _settings: storage)
    with pytest.raises(ObjectStorageError, match="fora do escopo"):
        await deletion._delete_r2("doc-1", {"visitor_id": owner, "object_keys": [victim_key]}, settings)
    assert storage.deleted_calls == []
    assert victim_key in storage.keys


@pytest.mark.asyncio
async def test_cleanup_dry_run_does_not_call_deletion(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    catalog = FakeCatalog()

    async def expired(_limit, _older_than):
        return [{"doc_id": "doc-1", "status": "ready", "expires_at": "2026-01-01T00:00:00+00:00"}]

    catalog.list_expired_documents = expired
    monkeypatch.setattr(deletion.Catalog, "ephemeral", lambda _settings: catalog)
    monkeypatch.setattr(deletion, "delete_document_async", lambda *_args: pytest.fail("dry-run mutated state"))
    result = await deletion.cleanup_expired_documents(settings, limit=1, older_than=timedelta(), dry_run=True)
    assert result == [{"doc_id": "doc-1", "status": "ready", "expires_at": "2026-01-01T00:00:00+00:00"}]
