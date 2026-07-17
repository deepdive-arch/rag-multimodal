from pathlib import Path

import pytest

from core.exceptions import InvalidMediaError, UnsupportedFileError
from core.exceptions import FileTooLargeError
from services.storage import file_type_for, safe_storage_path, sanitize_filename, save_upload_stream, sha256_for_path, stage_existing_file, storage_key_for_path, validate_signature


class UploadStub:
    def __init__(self, chunks: list[bytes]):
        self.chunks = iter(chunks)

    async def read(self, _size: int) -> bytes:
        return next(self.chunks, b"")


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
    path = tmp_path / "bad.txt"
    path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(UnsupportedFileError):
        validate_signature(path, ".txt")


@pytest.mark.asyncio
async def test_invalid_upload_is_removed_before_promotion(tmp_path: Path):
    from core.config import Settings

    settings = Settings(uploads_dir=tmp_path / "uploads", derived_dir=tmp_path / "derived", database_path=tmp_path / "db.sqlite")
    with pytest.raises(UnsupportedFileError):
        await save_upload_stream(UploadStub([b"\xff\xfe"]), "bad.txt", settings)
    assert list(settings.uploads_dir.glob(".*")) == []
    assert list(settings.uploads_dir.glob("*")) == []


@pytest.mark.asyncio
async def test_upload_above_limit_does_not_remain(tmp_path: Path):
    from core.config import Settings

    settings = Settings(uploads_dir=tmp_path / "uploads", derived_dir=tmp_path / "derived", database_path=tmp_path / "db.sqlite", max_upload_size_mb=1)
    with pytest.raises(FileTooLargeError):
        await save_upload_stream(UploadStub([b"x" * (1024 * 1024 + 1)]), "large.txt", settings)
    assert list(settings.uploads_dir.glob("*")) == []


def test_signature_validation_does_not_use_read_bytes(tmp_path: Path, monkeypatch):
    path = tmp_path / "document.pdf"
    path.write_bytes(b"%PDF-" + b"x" * 5000)
    monkeypatch.setattr(Path, "read_bytes", lambda _path: pytest.fail("read_bytes must not be used"))
    validate_signature(path, ".pdf")


def test_valid_docx_package_is_accepted(tmp_path: Path):
    from zipfile import ZipFile

    path = tmp_path / "valid.docx"
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
    validate_signature(path, ".docx")


@pytest.mark.parametrize("payload", [b"PK\x03\x04", b"not a zip"])
def test_invalid_docx_packages_are_rejected(tmp_path: Path, payload: bytes):
    path = tmp_path / "invalid.docx"
    path.write_bytes(payload)
    with pytest.raises(UnsupportedFileError):
        validate_signature(path, ".docx")


def test_existing_valid_destination_is_reused(tmp_path: Path):
    from core.config import Settings

    source = tmp_path / "hello.txt"
    source.write_text("hello", encoding="utf-8")
    settings = Settings(uploads_dir=tmp_path / "uploads", derived_dir=tmp_path / "derived", database_path=tmp_path / "db.sqlite")
    first = stage_existing_file(source, settings)
    second = stage_existing_file(source, settings)
    assert second.path == first.path
    assert list(settings.uploads_dir.glob(".*")) == []


def test_corrupt_existing_destination_is_not_reused(tmp_path: Path):
    from core.config import Settings

    source = tmp_path / "hello.txt"
    source.write_text("hello", encoding="utf-8")
    settings = Settings(uploads_dir=tmp_path / "uploads", derived_dir=tmp_path / "derived", database_path=tmp_path / "db.sqlite")
    stored = stage_existing_file(source, settings)
    stored.path.write_text("world", encoding="utf-8")
    with pytest.raises(InvalidMediaError):
        stage_existing_file(source, settings)
    assert stored.path.read_text(encoding="utf-8") == "world"
    assert list(settings.uploads_dir.glob(".*")) == []
