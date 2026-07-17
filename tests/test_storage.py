from pathlib import Path

import pytest

from core.exceptions import InvalidMediaError, UnsupportedFileError
from services.storage import file_type_for, safe_storage_path, sanitize_filename, sha256_for_path, stage_existing_file, storage_key_for_path


def test_sanitize_filename_and_extension():
    assert sanitize_filename(r"..\folder/name with spaces.PDF") == "name_with_spaces.pdf"
    assert file_type_for("report.PDF") == "pdf"


def test_storage_key_rejects_traversal(tmp_path: Path):
    from core.config import Settings

    settings = Settings(uploads_dir=tmp_path / "uploads", derived_dir=tmp_path / "derived", database_path=tmp_path / "db.sqlite")
    with pytest.raises(InvalidMediaError):
        safe_storage_path("uploads/../secret.txt", settings)


def test_stage_hash_and_relative_key(tmp_path: Path):
    from core.config import Settings

    source = tmp_path / "hello.txt"
    source.write_text("hello", encoding="utf-8")
    settings = Settings(uploads_dir=tmp_path / "uploads", derived_dir=tmp_path / "derived", database_path=tmp_path / "db.sqlite")
    stored = stage_existing_file(source, settings)
    assert stored.sha256 == sha256_for_path(stored.path)
    assert storage_key_for_path(stored.path, settings).startswith("uploads/")


def test_binary_named_as_text_is_rejected(tmp_path: Path):
    from services.storage import validate_signature

    path = tmp_path / "bad.txt"
    path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(UnsupportedFileError):
        validate_signature(path, ".txt")
