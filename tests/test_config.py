from pathlib import Path

import pytest

from core.config import Settings


def test_settings_create_directories(tmp_path: Path):
    settings = Settings(uploads_dir=tmp_path / "uploads", derived_dir=tmp_path / "derived", database_path=tmp_path / "db" / "rag.db")
    assert settings.uploads_dir.is_dir()
    assert settings.derived_dir.is_dir()
    assert settings.embedding_dimension == 1536


def test_invalid_overlap_is_rejected():
    with pytest.raises(ValueError):
        Settings(chunk_size=100, chunk_overlap=100)


def test_invalid_top_k_is_rejected():
    with pytest.raises(ValueError):
        Settings(default_top_k=10, max_top_k=5)
