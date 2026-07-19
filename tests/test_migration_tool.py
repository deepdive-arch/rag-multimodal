import asyncio
import json
import sqlite3
from pathlib import Path

from core.config import Settings
from services.storage import ObjectMetadata
from tools import migrate_local_persistence as migration


def _legacy_db(path: Path, doc_id: str, storage_key: str, *, media_key: str = "", vector_id: str = "vector-1") -> None:
    """Create the old schema used by the migration unit fixtures."""
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE ingested_files (
            doc_id TEXT PRIMARY KEY, original_name TEXT, stored_name TEXT,
            storage_key TEXT, file_type TEXT, mime_type TEXT, size_bytes INTEGER,
            status TEXT, chunks_count INTEGER, warnings_json TEXT,
            error_message TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY, doc_id TEXT, vector_id TEXT,
            chunk_index INTEGER, page_number INTEGER, content_modality TEXT,
            media_key TEXT, created_at TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO ingested_files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (doc_id, "notes.txt", "notes.txt", storage_key, "text", "text/plain", 5, "ready", 1, "[]", "secret-never-report", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    connection.execute(
        "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("chunk-1", doc_id, vector_id, 0, 0, "text", media_key, "2026-01-01T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()


def test_dry_run_reads_sqlite_without_mutating_source(tmp_path: Path):
    """The default mode reports the migration and leaves SQLite/files untouched."""
    sqlite_path = tmp_path / "rag.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "notes.txt").write_text("hello", encoding="utf-8")
    _legacy_db(sqlite_path, "11111111-1111-1111-1111-111111111111", "notes.txt")
    before = sqlite_path.read_bytes()
    report_path = tmp_path / "report.json"

    assert migration.main(["--sqlite-path", str(sqlite_path), "--uploads-dir", str(uploads), "--report-path", str(report_path)]) == 0

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["dry_run"] is True
    assert report["counts"]["documents_seen"] == 1
    assert "secret-never-report" not in report_path.read_text(encoding="utf-8")
    assert sqlite_path.read_bytes() == before
    assert (uploads / "notes.txt").read_text(encoding="utf-8") == "hello"


def test_dry_run_rejects_traversal_and_does_not_delete(tmp_path: Path):
    """A legacy path cannot escape the explicitly selected uploads root."""
    sqlite_path = tmp_path / "rag.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("do not touch", encoding="utf-8")
    _legacy_db(sqlite_path, "22222222-2222-2222-2222-222222222222", "../outside.txt")
    report_path = tmp_path / "report.json"

    assert migration.main(["--dry-run", "--sqlite-path", str(sqlite_path), "--uploads-dir", str(uploads), "--report-path", str(report_path)]) == 1

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["documents"][0]["errors"] == ["original_not_found"]
    assert outside.read_text(encoding="utf-8") == "do not touch"


def test_reindex_requires_explicit_apply():
    """Embedding regeneration is never enabled by a dry-run flag."""
    try:
        migration.main(["--reindex-missing"])
    except SystemExit as error:
        assert "requires --apply" in str(error)
    else:
        raise AssertionError("--reindex-missing must require --apply")


class FakeCatalog:
    """In-memory migration target with the idempotency constraints used by Postgres."""

    def __init__(self):
        self.documents = {}
        self.objects = {}
        self.chunks = {}
        self.object_specs = []

    async def get_file(self, doc_id):
        return self.documents.get(doc_id)

    async def find_by_sha256(self, sha256):
        return next((item for item in self.documents.values() if item["sha256"] == sha256), None)

    async def create_document(self, record):
        item = dict(record) | {"created": True, "storage_key": record["object_key"]}
        self.documents[record["doc_id"]] = item
        self.objects[record["object_key"]] = "original-object"
        return item

    async def register_objects(self, objects):
        self.object_specs.extend(objects)
        for index, item in enumerate(objects):
            self.objects.setdefault(item["object_key"], f"object-{index}")
        return dict(self.objects)

    async def register_chunks(self, chunks):
        for item in chunks:
            self.chunks.setdefault(item["chunk_id"], dict(item))

    async def get_vector_ids(self, doc_id):
        return [chunk_id for chunk_id, item in self.chunks.items() if item["doc_id"] == doc_id]

    async def close(self):
        return None


class FakeStorage:
    """R2 double that retains safe HEAD metadata between migration runs."""

    def __init__(self):
        self.objects = {}
        self.puts = []

    async def head_object(self, key):
        return self.objects.get(key)

    async def put_file(self, key, path, *, content_type, metadata):
        self.puts.append(key)
        result = ObjectMetadata(key, path.stat().st_size, content_type, metadata=dict(metadata))
        self.objects[key] = result
        return result


def _apply_args(sqlite_path: Path, uploads: Path):
    return migration._parser().parse_args(
        ["--apply", "--sqlite-path", str(sqlite_path), "--uploads-dir", str(uploads)]
    )


def test_apply_preserves_records_uploads_derived_and_is_idempotent(monkeypatch, tmp_path: Path):
    sqlite_path = tmp_path / "rag.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "notes.txt").write_text("hello", encoding="utf-8")
    (uploads / "page.png").write_bytes(b"derived")
    doc_id = "33333333-3333-3333-3333-333333333333"
    _legacy_db(sqlite_path, doc_id, "notes.txt", media_key="page.png")
    before = sqlite_path.read_bytes()
    catalog = FakeCatalog()
    storage = FakeStorage()
    settings = Settings(_env_file=None, app_env="test", temp_processing_dir=tmp_path / "processing")
    monkeypatch.setattr(migration, "get_settings", lambda: settings)
    monkeypatch.setattr(migration.Catalog, "ephemeral", lambda _settings: catalog)
    monkeypatch.setattr(migration, "get_object_storage", lambda _settings: storage)
    monkeypatch.setattr(migration, "process_uploaded_document", lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected embedding regeneration")))

    first = asyncio.run(migration._run(_apply_args(sqlite_path, uploads)))
    second = asyncio.run(migration._run(_apply_args(sqlite_path, uploads)))

    document = catalog.documents[doc_id]
    assert first["counts"] == {"documents_seen": 1, "created": 1, "already_migrated": 0, "errors": 0}
    assert second["counts"] == {"documents_seen": 1, "created": 0, "already_migrated": 1, "errors": 0}
    assert document["doc_id"] == doc_id
    assert document["status"] == "ready"
    assert document["created_at"] == "2026-01-01T00:00:00+00:00"
    assert document["sha256"] == migration.sha256_for_path(uploads / "notes.txt")
    assert set(catalog.chunks) == {"vector-1"}
    assert catalog.chunks["vector-1"]["object_id"]
    assert {item["object_kind"] for item in catalog.object_specs} == {"original", "derived"}
    assert len(storage.puts) == 2
    assert sqlite_path.read_bytes() == before
    assert (uploads / "notes.txt").is_file() and (uploads / "page.png").is_file()


def test_apply_rejects_doc_id_hash_conflict_before_upload(monkeypatch, tmp_path: Path):
    sqlite_path = tmp_path / "rag.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "notes.txt").write_text("hello", encoding="utf-8")
    doc_id = "44444444-4444-4444-4444-444444444444"
    _legacy_db(sqlite_path, doc_id, "notes.txt")
    catalog = FakeCatalog()
    catalog.documents[doc_id] = {"doc_id": doc_id, "sha256": "f" * 64}
    storage = FakeStorage()
    settings = Settings(_env_file=None, app_env="test", temp_processing_dir=tmp_path / "processing")
    monkeypatch.setattr(migration, "get_settings", lambda: settings)
    monkeypatch.setattr(migration.Catalog, "ephemeral", lambda _settings: catalog)
    monkeypatch.setattr(migration, "get_object_storage", lambda _settings: storage)

    report = asyncio.run(migration._run(_apply_args(sqlite_path, uploads)))

    assert report["documents"][0]["errors"] == ["doc_id_conflict"]
    assert report["counts"]["errors"] == 1
    assert storage.puts == []


def test_reindex_option_detects_provider_missing_vector(monkeypatch, tmp_path: Path):
    class MissingIndex:
        def fetch(self, **_kwargs):
            return {"vectors": {}}

    catalog = FakeCatalog()
    catalog.chunks["vector-1"] = {"doc_id": "doc"}
    settings = Settings(_env_file=None, app_env="test", temp_processing_dir=tmp_path / "processing")
    chunks = [migration.LegacyChunk("chunk-1", "doc", "vector-1", 0, 0, "text", "")]
    monkeypatch.setattr(migration, "get_index", lambda _settings: MissingIndex())

    assert asyncio.run(migration._requires_reindex(catalog, settings, "doc", chunks)) is True
