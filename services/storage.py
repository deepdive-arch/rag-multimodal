"""Safe local storage and streaming upload helpers."""

import hashlib
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import filetype

from core.config import Settings, get_settings
from core.exceptions import FileTooLargeError, InvalidMediaError, UnsupportedFileError


logger = logging.getLogger("rag_multimodal.storage")


TEXT_EXTENSIONS = {".txt", ".md"}
DOCUMENT_EXTENSIONS = {".docx", ".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}
AUDIO_EXTENSIONS = {".mp3", ".wav"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS | IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
MIME_MAP = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}


@dataclass(frozen=True)
class StoredUpload:
    """Result of a streamed upload."""

    path: Path
    sha256: str
    stored_name: str
    size_bytes: int


def sanitize_filename(filename: str | None) -> str:
    """Return a safe basename while preserving a normalized extension."""
    basename = Path(filename or "upload").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(basename).stem).strip("._") or "upload"
    suffix = Path(basename).suffix.lower()
    return f"{stem}{suffix}"


def extension_for(filename: str | Path) -> str:
    """Return a normalized supported extension."""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileError(f"Extensão não suportada: {suffix or 'ausente'}")
    return suffix


def file_type_for(filename: str | Path) -> str:
    """Map a filename extension to the public file type."""
    suffix = extension_for(filename)
    if suffix in TEXT_EXTENSIONS:
        return "text"
    if suffix == ".docx":
        return "docx"
    if suffix == ".pdf":
        return "pdf"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return "audio"


def mime_for(filename: str | Path) -> str:
    """Return the expected MIME type for a supported extension."""
    return MIME_MAP[extension_for(filename)]


def sha256_for_path(path: Path) -> str:
    """Hash a file incrementally."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def storage_key_for_path(path: Path, settings: Settings | None = None) -> str:
    """Convert an allowed local path into a relative storage key."""
    settings = settings or get_settings()
    root = settings.uploads_dir.resolve()
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError as error:
        raise InvalidMediaError("Arquivo fora do armazenamento permitido") from error
    return f"uploads/{relative}"


def safe_storage_path(storage_key: str, settings: Settings | None = None) -> Path:
    """Resolve a validated relative storage key inside the uploads root."""
    settings = settings or get_settings()
    normalized = storage_key.replace("\\", "/")
    if not normalized.startswith("uploads/") or Path(normalized).is_absolute() or ".." in Path(normalized).parts:
        raise InvalidMediaError("Storage key inválida")
    root = settings.uploads_dir.resolve()
    candidate = (root / Path(normalized.removeprefix("uploads/"))).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise InvalidMediaError("Storage key fora do diretório permitido") from error
    return candidate


def validate_signature(path: Path, extension: str) -> None:
    """Validate the real file signature without loading the whole file."""
    with path.open("rb") as stream:
        header = stream.read(4096)
    kind = filetype.guess(header)
    known_mime = kind.mime if kind else ""
    if extension in TEXT_EXTENSIONS:
        _validate_text_header(header)
        return
    if extension == ".pdf" and not header.startswith(b"%PDF-"):
        raise UnsupportedFileError("Assinatura PDF inválida")
    if extension == ".docx":
        _validate_docx_package(path)
        return
    if extension in IMAGE_EXTENSIONS and not known_mime.startswith("image/"):
        raise UnsupportedFileError("Assinatura de imagem inválida")
    if extension in AUDIO_EXTENSIONS and known_mime and not known_mime.startswith("audio/") and not header.startswith((b"ID3", b"RIFF")):
        raise UnsupportedFileError("Assinatura de áudio inválida")
    if extension in VIDEO_EXTENSIONS and not (b"ftyp" in header[:64] or known_mime.startswith("video/")):
        raise UnsupportedFileError("Assinatura de vídeo inválida")


def _validate_text_header(header: bytes) -> None:
    """Reject a text upload whose sampled bytes are not UTF-8."""
    try:
        header.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UnsupportedFileError("Arquivo de texto não é UTF-8 válido") from error


def _validate_docx_package(path: Path) -> None:
    """Require the two structural entries that identify a DOCX package."""
    try:
        with zipfile.ZipFile(path) as archive:
            required = {"[Content_Types].xml", "word/document.xml"}
            if not required.issubset(archive.namelist()):
                raise UnsupportedFileError("Assinatura DOCX inválida")
    except zipfile.BadZipFile as error:
        raise UnsupportedFileError("Assinatura DOCX inválida") from error


def _temporary_path(settings: Settings, prefix: str) -> Path:
    """Create a unique path inside the controlled storage directory."""
    return settings.uploads_dir / f".{prefix}-{uuid4().hex}"


def _copy_path(source: Path, temporary: Path, limit: int) -> tuple[str, int]:
    """Copy a path incrementally while enforcing the configured limit."""
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as reader, temporary.open("wb") as writer:
        for block in iter(lambda: reader.read(1024 * 1024), b""):
            size += len(block)
            if size > limit:
                raise FileTooLargeError("Arquivo excede o limite configurado")
            digest.update(block)
            writer.write(block)
    return digest.hexdigest(), size


def _reuse_existing(destination: Path, temporary: Path, sha256: str, size: int, extension: str) -> StoredUpload | None:
    """Reuse a destination only after checking its integrity and signature."""
    if not destination.exists():
        return None
    try:
        valid_integrity = destination.is_file() and destination.stat().st_size == size and sha256_for_path(destination) == sha256
    except OSError as error:
        _reject_existing_destination("stat_or_hash_error", error)
    if not valid_integrity:
        _reject_existing_destination("integrity_mismatch")
    try:
        validate_signature(destination, extension)
    except UnsupportedFileError as error:
        _reject_existing_destination("signature_invalid", error)
    temporary.unlink(missing_ok=True)
    return StoredUpload(destination, sha256, destination.name, size)


def _reject_existing_destination(reason: str, error: Exception | None = None) -> None:
    """Log safe integrity evidence and reject a conflicting destination."""
    logger.error("storage_destination_integrity_failed", extra={"reason": reason})
    raise InvalidMediaError("Arquivo existente no storage não pôde ser validado") from error


async def save_upload_stream(upload: object, filename: str | None, settings: Settings | None = None) -> StoredUpload:
    """Save an UploadFile-like object in bounded chunks while validating before promotion."""
    settings = settings or get_settings()
    safe_name = sanitize_filename(filename)
    extension = extension_for(safe_name)
    temporary = _temporary_path(settings, "uploading")
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("wb") as target:
            while True:
                block = await upload.read(1024 * 1024)  # type: ignore[attr-defined]
                if not block:
                    break
                size += len(block)
                if size > settings.max_upload_size_bytes:
                    raise FileTooLargeError(f"Arquivo excede o limite de {settings.max_upload_size_mb} MB")
                digest.update(block)
                target.write(block)
        sha256 = digest.hexdigest()
        destination = settings.uploads_dir / f"{sha256}_{safe_name}"
        validate_signature(temporary, extension)
        reused = _reuse_existing(destination, temporary, sha256, size, extension)
        if reused:
            return reused
        temporary.replace(destination)
        return StoredUpload(destination, sha256, destination.name, size)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def stage_existing_file(path: Path, settings: Settings | None = None) -> StoredUpload:
    """Copy a CLI file into permanent storage without loading it in memory."""
    settings = settings or get_settings()
    source = path.resolve()
    if not source.is_file():
        raise InvalidMediaError("Arquivo de entrada não encontrado")
    safe_name = sanitize_filename(source.name)
    extension = extension_for(safe_name)
    temporary = _temporary_path(settings, "staging")
    try:
        sha256, size = _copy_path(source, temporary, settings.max_upload_size_bytes)
        destination = settings.uploads_dir / f"{sha256}_{safe_name}"
        validate_signature(temporary, extension)
        reused = _reuse_existing(destination, temporary, sha256, size, extension)
        if reused:
            return reused
        temporary.replace(destination)
        return StoredUpload(destination, sha256, destination.name, size)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def remove_storage(storage_key: str, settings: Settings | None = None) -> None:
    """Remove one stored file and its derived document directory."""
    settings = settings or get_settings()
    path = safe_storage_path(storage_key, settings)
    path.unlink(missing_ok=True)
    if "/derived/" not in storage_key:
        doc_id = Path(path).name.split("_", 1)[0]
        shutil.rmtree(settings.derived_dir / doc_id, ignore_errors=True)
