from pathlib import Path

from core.config import Settings
from services.ingestion import ingest_file


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
