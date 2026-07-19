import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

from core.config import Settings
from core.exceptions import IngestionError, InvalidMediaError, UnsupportedFileError
from db.catalog import Catalog
from services.ingestion import ingest_file
from services.media import MediaItem
from services.storage import ObjectMetadata, sha256_for_path


pytestmark = pytest.mark.integration
TEST_VISITOR_ID = str(uuid4())


class FakeR2Storage:
    """Per-test durable-object double; local paths remain processing-only."""

    def __init__(self):
        self.objects = {}

    async def put_file(self, key, source, *, content_type, metadata=None):
        self.objects[key] = (Path(source).read_bytes(), content_type, dict(metadata or {}))
        return ObjectMetadata(key, Path(source).stat().st_size, content_type, metadata=dict(metadata or {}))

    async def download_to_path(self, key, destination):
        data, _content_type, _metadata = self.objects[key]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return destination

    async def list_objects_by_prefix(self, prefix):
        return [ObjectMetadata(key, len(data), content_type, metadata=metadata) for key, (data, content_type, metadata) in self.objects.items() if key.startswith(f"{prefix}/")]

    async def delete_objects(self, keys):
        for key in keys:
            self.objects.pop(key, None)


async def _clear_catalog() -> None:
    catalog = Catalog(os.environ["TEST_DATABASE_URL"])
    try:
        await catalog.clear_catalog()
    finally:
        await catalog.close()


@pytest.fixture(autouse=True)
def isolated_ingestion_state(test_schema, monkeypatch):
    """Reset Postgres and replace R2 for every ingestion integration case."""
    asyncio.run(_clear_catalog())
    storage = FakeR2Storage()
    monkeypatch.setattr("services.ingestion.get_object_storage", lambda _settings: storage)
    yield storage
    asyncio.run(_clear_catalog())


def _settings(tmp_path: Path) -> Settings:
    """Build isolated integration settings."""
    return Settings(_env_file=None, app_env="test", database_url=os.environ["TEST_DATABASE_URL"], temp_processing_dir=tmp_path / "processing")


def _read_record(settings: Settings, sha256: str):
    """Read and close a temporary catalog."""
    async def read():
        catalog = Catalog(settings.database_url)
        try:
            return await catalog.get_file(sha256, TEST_VISITOR_ID)
        finally:
            await catalog.close()
    return asyncio.run(read())


def test_ingestion_success_and_duplicate(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    source = settings.temp_processing_dir / "file.txt"
    source.write_text("Conteúdo suficientemente longo para ser indexado.", encoding="utf-8")
    monkeypatch.setattr("services.ingestion.get_settings", lambda: settings)
    monkeypatch.setattr("services.ingestion.embed_text", lambda text, settings: [0.0] * settings.embedding_dimension)
    monkeypatch.setattr("services.ingestion.upsert_vectors", lambda vectors, settings, visitor_id: None)
    first = ingest_file(source, "file.txt", visitor_id=TEST_VISITOR_ID)
    second = ingest_file(source, "file.txt", visitor_id=TEST_VISITOR_ID)
    assert first.duplicate is False
    assert second.duplicate is True


def test_ingestion_preserves_expected_error(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    source = settings.temp_processing_dir / "bad.txt"
    source.write_text("conteúdo", encoding="utf-8")
    monkeypatch.setattr("services.ingestion.get_settings", lambda: settings)
    monkeypatch.setattr("services.ingestion.extract_items", lambda *args: (_ for _ in ()).throw(UnsupportedFileError("Assinatura inválida")))
    with pytest.raises(UnsupportedFileError, match="Assinatura"):
        ingest_file(source, "bad.txt", visitor_id=TEST_VISITOR_ID)
    record = _read_record(settings, sha256_for_path(source))
    assert record["status"] == "failed"
    assert record["error_message"] == "Assinatura inválida"


def test_unexpected_ingestion_error_is_generic(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    source = settings.temp_processing_dir / "broken.txt"
    source.write_text("conteúdo", encoding="utf-8")
    monkeypatch.setattr("services.ingestion.get_settings", lambda: settings)
    monkeypatch.setattr("services.ingestion.extract_items", lambda *args: (_ for _ in ()).throw(RuntimeError("path secret")))
    with pytest.raises(IngestionError, match="inesperada"):
        ingest_file(source, "broken.txt", visitor_id=TEST_VISITOR_ID)


def test_zero_items_are_not_marked_ready(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    source = settings.temp_processing_dir / "empty.txt"
    source.write_text("conteúdo", encoding="utf-8")
    monkeypatch.setattr("services.ingestion.get_settings", lambda: settings)
    monkeypatch.setattr("services.ingestion.extract_items", lambda *args: [])
    with pytest.raises(InvalidMediaError, match="indexável"):
        ingest_file(source, "empty.txt", visitor_id=TEST_VISITOR_ID)


def test_rollback_attempts_vector_cleanup(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    source = settings.temp_processing_dir / "rollback.txt"
    source.write_text("conteúdo", encoding="utf-8")
    deleted: list[str] = []
    monkeypatch.setattr("services.ingestion.get_settings", lambda: settings)
    monkeypatch.setattr("services.ingestion.extract_items", lambda *args: [MediaItem("conteúdo", "text", "text")])
    monkeypatch.setattr("services.ingestion.embed_text", lambda text, current: [0.0] * current.embedding_dimension)
    monkeypatch.setattr("services.ingestion.delete_vectors", lambda ids, current, visitor_id: deleted.extend(ids))
    monkeypatch.setattr("services.ingestion.upsert_vectors", lambda *args: (_ for _ in ()).throw(RuntimeError("pinecone")))
    with pytest.raises(IngestionError):
        ingest_file(source, "rollback.txt", visitor_id=TEST_VISITOR_ID)
    assert deleted


def test_rollback_removes_chunks_and_derived_files(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    source = settings.temp_processing_dir / "rollback.txt"
    source.write_text("conteúdo", encoding="utf-8")
    derived_paths = []
    original_register_chunks = Catalog.register_chunks

    async def register_chunks_then_fail(catalog, chunks):
        await original_register_chunks(catalog, chunks)
        raise RuntimeError("pinecone")

    monkeypatch.setattr("services.ingestion.get_settings", lambda: settings)
    def extracted(_candidate, doc_id, current):
        derived = current.temp_processing_dir / doc_id / "derived" / "page.png"
        derived.parent.mkdir(parents=True, exist_ok=True)
        derived.write_bytes(b"derived")
        derived_paths.append(derived)
        return [MediaItem("", "image", "image", 1, derived, "image/png")]

    monkeypatch.setattr("services.ingestion.extract_items", extracted)
    monkeypatch.setattr("services.ingestion.embed_media", lambda data, mime, current: [0.0] * current.embedding_dimension)
    monkeypatch.setattr("services.ingestion.upsert_vectors", lambda vectors, current, visitor_id: None)
    monkeypatch.setattr("services.ingestion.delete_vectors", lambda ids, current, visitor_id: None)
    monkeypatch.setattr(Catalog, "register_chunks", register_chunks_then_fail)
    with pytest.raises(IngestionError):
        ingest_file(source, "rollback.txt", visitor_id=TEST_VISITOR_ID)
    record = _read_record(settings, sha256_for_path(source))
    assert record["status"] == "failed"
    assert derived_paths and not derived_paths[0].exists()
