from pathlib import Path

import fitz
import pytest
from PIL import Image

from api.server import _source_response
from core.config import Settings
from core.exceptions import ExternalServiceError, ObjectStorageError
from core.visitor import new_visitor_id
from services import ingestion
from services.media import MediaItem
from services.retrieval import RetrievedSource
from services.storage import ObjectMetadata, original_object_key, sha256_for_path


class FakeCatalog:
    def __init__(self):
        self.statuses = []
        self.rows = []

    async def update_status(self, _doc_id, status, **_kwargs):
        self.statuses.append(status)

    async def register_objects(self, objects):
        return {item["object_key"]: f"object-{index}" for index, item in enumerate(objects)}

    async def register_chunks(self, rows):
        self.rows.extend(rows)

    async def clear_processing_artifacts(self, _doc_id):
        return None


class FakeStorage:
    def __init__(self, failure=None):
        self.failure = failure
        self.puts = []
        self.deleted = []

    async def put_file(self, key, source, **_kwargs):
        if self.failure == "r2":
            raise ObjectStorageError("R2 indisponível")
        self.puts.append((key, Path(source)))
        return ObjectMetadata(key, Path(source).stat().st_size, "image/png")

    async def list_objects_by_prefix(self, prefix):
        return [
            ObjectMetadata(key, 1, "application/octet-stream") for key, _ in self.puts if key.startswith(f"{prefix}/")
        ]

    async def delete_objects(self, keys):
        self.deleted.extend(keys)

    async def head_object(self, _key):
        return ObjectMetadata("key", 1, "image/png")

    def generate_presigned_get_url_sync(self, _key, **_kwargs):
        return "https://r2.example/signed"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="",
        temp_processing_dir=tmp_path / "processing",
    )


def _record(candidate: Path, settings: Settings):
    visitor_id = new_visitor_id()
    object_key = original_object_key("doc-1", "evidence.txt", settings, visitor_id)
    return {
        "doc_id": "doc-1",
        "original_name": "evidence.txt",
        "sanitized_name": "evidence.txt",
        "object_key": object_key,
        "storage_key": object_key,
        "object_id": "original-object",
        "file_type": "text",
        "mime_type": "text/plain",
        "sha256": sha256_for_path(candidate),
        "size_bytes": candidate.stat().st_size,
        "status": "processing",
        "visitor_id": visitor_id,
    }


@pytest.mark.parametrize("suffix", [".txt", ".pdf", ".png"])
def test_txt_pdf_and_image_are_extracted_without_persistent_local_catalog(tmp_path, suffix):
    settings = _settings(tmp_path)
    path = tmp_path / f"fixture{suffix}"
    if suffix == ".txt":
        path.write_text("Texto textual de evidência." * 10, encoding="utf-8")
    elif suffix == ".pdf":
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "Texto textual extraído de PDF.")
        document.save(path)
        document.close()
    else:
        Image.new("RGB", (20, 20), "red").save(path, "PNG")

    items = ingestion.extract_items(path, "doc-1", settings)

    assert items
    assert not list(settings.temp_processing_dir.rglob("*.db"))
    assert all(".db" not in str(item.media_path or "") for item in items)


@pytest.mark.asyncio
async def test_pipeline_orders_indexing_and_ready_and_keeps_safe_metadata(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    candidate = tmp_path / "evidence.txt"
    candidate.write_text("Conteúdo de evidência suficientemente longo.", encoding="utf-8")
    record = _record(candidate, settings)
    catalog = FakeCatalog()
    storage = FakeStorage()
    monkeypatch.setattr(ingestion, "get_object_storage", lambda _settings: storage)
    monkeypatch.setattr(ingestion, "extract_items", lambda *_args: [MediaItem("texto", "text", "text")])
    monkeypatch.setattr(ingestion, "embed_text", lambda *_args: [0.0] * settings.embedding_dimension)
    monkeypatch.setattr(
        ingestion,
        "upsert_vectors",
        lambda vectors, _settings, _visitor_id: storage.puts.extend((item["id"], Path("transient")) for item in vectors),
    )

    result = await ingestion._process_document(catalog, candidate, record, "doc-1", settings)

    assert result.chunks == 1
    assert catalog.statuses == ["indexing", "ready"]
    assert catalog.rows[0]["chunk_id"] == "doc-1-chunk-0"
    metadata = storage.puts[0] if storage.puts else None
    assert all(value not in str(metadata) for value in ("C:\\", "/tmp/"))
    assert not (settings.temp_processing_dir / "doc-1" / "derived").exists()


@pytest.mark.parametrize("failure", ["r2", "gemini", "pinecone"])
@pytest.mark.asyncio
async def test_pipeline_failures_never_mark_ready_and_clean_processing(monkeypatch, tmp_path, failure):
    settings = _settings(tmp_path)
    candidate = tmp_path / "evidence.txt"
    candidate.write_text("Conteúdo de evidência suficientemente longo.", encoding="utf-8")
    record = _record(candidate, settings)
    catalog = FakeCatalog()
    storage = FakeStorage(failure=failure)
    derived_dir = settings.temp_processing_dir / "doc-1" / "derived"
    derived_dir.mkdir(parents=True)
    derived = derived_dir / "page.png"
    derived.write_bytes(b"png")
    monkeypatch.setattr(ingestion, "get_object_storage", lambda _settings: storage)
    monkeypatch.setattr(
        ingestion, "extract_items", lambda *_args: [MediaItem("", "pdf", "image", 1, derived, "image/png")]
    )
    monkeypatch.setattr(
        ingestion, "embed_media", lambda *_args: (_ for _ in ()).throw(ExternalServiceError("Gemini falhou"))
    )
    monkeypatch.setattr(ingestion, "embed_text", lambda *_args: [0.0] * settings.embedding_dimension)
    if failure == "gemini":
        storage.failure = None
    if failure == "pinecone":
        storage.failure = None
        monkeypatch.setattr(ingestion, "embed_media", lambda *_args: [0.0] * settings.embedding_dimension)
        monkeypatch.setattr(
            ingestion, "upsert_vectors", lambda *_args: (_ for _ in ()).throw(ExternalServiceError("Pinecone falhou"))
        )

    with pytest.raises(Exception):
        await ingestion._process_document(catalog, candidate, record, "doc-1", settings)

    assert "ready" not in catalog.statuses
    assert catalog.statuses[-1] == "failed"
    assert not (settings.temp_processing_dir / "doc-1" / "derived").exists()


@pytest.mark.asyncio
async def test_failed_retry_claims_once_and_cleans_isolated_workspace(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    stored = tmp_path / "stored.txt"
    stored.write_text("Conteúdo persistido para retry.", encoding="utf-8")
    record = _record(stored, settings) | {"status": "failed", "chunks_count": 0, "warnings": []}

    class RetryCatalog(FakeCatalog):
        def __init__(self):
            super().__init__()
            self.state = "failed"
            self.claims = 0

        async def get_file(self, _doc_id):
            return record | {"status": self.state}

        async def claim_retry(self, _doc_id):
            if self.state != "failed":
                return False
            self.claims += 1
            self.state = "processing"
            return True

        async def get_vector_ids(self, _doc_id):
            return []

        async def update_status(self, _doc_id, status, **kwargs):
            self.state = status
            self.statuses.append(status)

        async def close(self):
            return None

    class RetryStorage(FakeStorage):
        async def download_to_path(self, _key, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(stored.read_bytes())
            return destination

    catalog = RetryCatalog()
    storage = RetryStorage()
    monkeypatch.setattr(ingestion.Catalog, "ephemeral", lambda _settings: catalog)
    monkeypatch.setattr(ingestion, "get_object_storage", lambda _settings: storage)
    monkeypatch.setattr(ingestion, "extract_items", lambda *_args: [MediaItem("texto", "text", "text")])
    monkeypatch.setattr(ingestion, "embed_text", lambda *_args: [0.0] * settings.embedding_dimension)
    monkeypatch.setattr(ingestion, "upsert_vectors", lambda *_args: None)

    result = await ingestion._retry_document("doc-1", settings)
    duplicate = await ingestion._retry_document("doc-1", settings)

    assert result is not None
    assert duplicate is None
    assert catalog.claims == 1
    assert catalog.statuses == ["indexing", "ready"]
    assert not (settings.temp_processing_dir / "doc-1").exists()


def test_media_source_response_is_presigned_on_demand_and_never_exposes_key(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    key = original_object_key("doc-1", "image.png", settings)
    storage = FakeStorage()
    monkeypatch.setattr("api.server.get_object_storage", lambda _settings: storage)
    source = RetrievedSource(
        "doc-1", "doc-1-chunk-0", "image.png", "", "image", "image/png", "image", 1, "", "", key, 0.0, 0.9
    )

    response = _source_response(source, settings)
    payload = response.model_dump()

    assert payload["media_url"] == "https://r2.example/signed"
    assert key not in str(payload)
    assert str(tmp_path) not in str(payload)
