import asyncio
from pathlib import Path

import pytest

from core.config import Settings
from core.exceptions import IngestionError, InvalidMediaError, UnsupportedFileError
from db.catalog import Catalog
from services.media import MediaItem
from services.ingestion import ingest_file
from services.storage import sha256_for_path


def test_ingestion_success_and_duplicate(monkeypatch, tmp_path: Path):
    settings = Settings(uploads_dir=tmp_path / "uploads", derived_dir=tmp_path / "derived", database_path=tmp_path / "db.sqlite")
    source = settings.uploads_dir / "file.txt"
    source.write_text("Conteúdo suficientemente longo para ser indexado.", encoding="utf-8")
    monkeypatch.setattr("services.ingestion.get_settings", lambda: settings)
    monkeypatch.setattr("services.ingestion.embed_text", lambda text, settings: [0.0] * settings.embedding_dimension)
    monkeypatch.setattr("services.ingestion.upsert_vectors", lambda vectors, settings: None)
    first = ingest_file(source, "file.txt")
    second = ingest_file(source, "file.txt")
    assert first.duplicate is False
    assert second.duplicate is True


def test_ingestion_preserves_expected_error(monkeypatch, tmp_path: Path):
    settings = Settings(uploads_dir=tmp_path / "uploads", derived_dir=tmp_path / "derived", database_path=tmp_path / "db.sqlite")
    source = settings.uploads_dir / "bad.txt"
    source.write_text("conteúdo", encoding="utf-8")
    monkeypatch.setattr("services.ingestion.get_settings", lambda: settings)
    monkeypatch.setattr("services.ingestion.extract_items", lambda *args: (_ for _ in ()).throw(UnsupportedFileError("Assinatura inválida")))
    with pytest.raises(UnsupportedFileError, match="Assinatura"):
        ingest_file(source, "bad.txt")
    record = asyncio.run(Catalog(settings.database_path).get_file(sha256_for_path(source)))
    assert record["status"] == "failed"
    assert record["error_message"] == "Assinatura inválida"


def test_unexpected_ingestion_error_is_generic(monkeypatch, tmp_path: Path):
    settings = Settings(uploads_dir=tmp_path / "uploads", derived_dir=tmp_path / "derived", database_path=tmp_path / "db.sqlite")
    source = settings.uploads_dir / "broken.txt"
    source.write_text("conteúdo", encoding="utf-8")
    monkeypatch.setattr("services.ingestion.get_settings", lambda: settings)
    monkeypatch.setattr("services.ingestion.extract_items", lambda *args: (_ for _ in ()).throw(RuntimeError("path secret")))
    with pytest.raises(IngestionError, match="inesperada"):
        ingest_file(source, "broken.txt")


def test_zero_items_are_not_marked_ready(monkeypatch, tmp_path: Path):
    settings = Settings(uploads_dir=tmp_path / "uploads", derived_dir=tmp_path / "derived", database_path=tmp_path / "db.sqlite")
    source = settings.uploads_dir / "empty.txt"
    source.write_text("conteúdo", encoding="utf-8")
    monkeypatch.setattr("services.ingestion.get_settings", lambda: settings)
    monkeypatch.setattr("services.ingestion.extract_items", lambda *args: [])
    with pytest.raises(InvalidMediaError, match="indexável"):
        ingest_file(source, "empty.txt")


def test_rollback_attempts_vector_cleanup(monkeypatch, tmp_path: Path):
    settings = Settings(uploads_dir=tmp_path / "uploads", derived_dir=tmp_path / "derived", database_path=tmp_path / "db.sqlite")
    source = settings.uploads_dir / "rollback.txt"
    source.write_text("conteúdo", encoding="utf-8")
    deleted: list[str] = []
    monkeypatch.setattr("services.ingestion.get_settings", lambda: settings)
    monkeypatch.setattr("services.ingestion.extract_items", lambda *args: [MediaItem("conteúdo", "text", "text")])
    monkeypatch.setattr("services.ingestion.embed_text", lambda text, current: [0.0] * current.embedding_dimension)
    monkeypatch.setattr("services.ingestion.delete_vectors", lambda ids, current: deleted.extend(ids))
    monkeypatch.setattr("services.ingestion.upsert_vectors", lambda *args: (_ for _ in ()).throw(RuntimeError("pinecone")))
    with pytest.raises(IngestionError):
        ingest_file(source, "rollback.txt")
    assert deleted


def test_rollback_removes_chunks_and_derived_files(monkeypatch, tmp_path: Path):
    settings = Settings(uploads_dir=tmp_path / "uploads", derived_dir=tmp_path / "derived", database_path=tmp_path / "db.sqlite")
    source = settings.uploads_dir / "rollback.txt"
    source.write_text("conteúdo", encoding="utf-8")
    derived = settings.derived_dir / sha256_for_path(source)
    derived.mkdir(parents=True)
    (derived / "page.png").write_bytes(b"derived")
    original_add_chunks = Catalog.add_chunks

    async def add_chunks_then_fail(catalog, chunks):
        await original_add_chunks(catalog, chunks)
        raise RuntimeError("pinecone")

    monkeypatch.setattr("services.ingestion.get_settings", lambda: settings)
    monkeypatch.setattr("services.ingestion.extract_items", lambda *args: [MediaItem("conteúdo", "text", "text")])
    monkeypatch.setattr("services.ingestion.embed_text", lambda text, current: [0.0] * current.embedding_dimension)
    monkeypatch.setattr("services.ingestion.upsert_vectors", lambda vectors, current: None)
    monkeypatch.setattr("services.ingestion.delete_vectors", lambda ids, current: None)
    monkeypatch.setattr(Catalog, "add_chunks", add_chunks_then_fail)
    with pytest.raises(IngestionError):
        ingest_file(source, "rollback.txt")
    record = asyncio.run(Catalog(settings.database_path).get_file(sha256_for_path(source)))
    assert record["status"] == "failed"
    assert asyncio.run(Catalog(settings.database_path).get_vector_ids(sha256_for_path(source))) == []
    assert not derived.exists()
