"""Safe local storage and streaming upload helpers."""

import hashlib
import re
import shutil
from uuid import uuid4
from dataclasses import dataclass
from pathlib import Path
import filetype

from core.config import Settings, get_settings
from core.exceptions import FileTooLargeError, InvalidMediaError, UnsupportedFileError


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
    """Validate the real file signature enough to reject renamed uploads."""
    header = path.read_bytes()[:4096]
    kind = filetype.guess(header)
    known_mime = kind.mime if kind else ""
    if extension in {".txt", ".md"}:
        try:
            header.decode("utf-8")
        except UnicodeDecodeError as error:
            raise UnsupportedFileError("Arquivo de texto não é UTF-8 válido") from error
        return
    if extension == ".pdf" and not header.startswith(b"%PDF-"):
        raise UnsupportedFileError("Assinatura PDF inválida")
    if extension == ".docx" and (not header.startswith(b"PK") or b"[Content_Types].xml" not in path.read_bytes()[:1024 * 1024]):
        raise UnsupportedFileError("Assinatura DOCX inválida")
    if extension in {".png", ".jpg", ".jpeg", ".gif", ".webp"} and not known_mime.startswith("image/"):
        raise UnsupportedFileError("Assinatura de imagem inválida")
    if extension in {".mp3", ".wav"} and known_mime and not known_mime.startswith("audio/") and not header.startswith((b"ID3", b"RIFF")):
        raise UnsupportedFileError("Assinatura de áudio inválida")
    if extension in {".mp4", ".mov"} and not (b"ftyp" in header[:64] or known_mime.startswith("video/")):
        raise UnsupportedFileError("Assinatura de vídeo inválida")


async def save_upload_stream(upload: object, filename: str | None, settings: Settings | None = None) -> StoredUpload:
    """Save an UploadFile-like object in bounded chunks while hashing it."""
    settings = settings or get_settings()
    safe_name = sanitize_filename(filename)
    extension = extension_for(safe_name)
    temporary = settings.uploads_dir / f".uploading-{uuid4().hex}"
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
        stored_name = f"{sha256}_{safe_name}"
        destination = settings.uploads_dir / stored_name
        if destination.exists():
            temporary.unlink(missing_ok=True)
            return StoredUpload(destination, sha256, stored_name, size)
        temporary.replace(destination)
        validate_signature(destination, extension)
        return StoredUpload(destination, sha256, stored_name, size)
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
    size = source.stat().st_size
    if size > settings.max_upload_size_bytes:
        raise FileTooLargeError(f"Arquivo excede o limite de {settings.max_upload_size_mb} MB")
    sha256 = sha256_for_path(source)
    destination = settings.uploads_dir / f"{sha256}_{safe_name}"
    if source != destination and not destination.exists():
        shutil.copyfile(source, destination)
    validate_signature(destination, extension)
    return StoredUpload(destination, sha256, destination.name, size)


def remove_storage(storage_key: str, settings: Settings | None = None) -> None:
    """Remove one stored file and its derived document directory."""
    settings = settings or get_settings()
    path = safe_storage_path(storage_key, settings)
    path.unlink(missing_ok=True)
    if "/derived/" not in storage_key:
        doc_id = Path(path).name.split("_", 1)[0]
        shutil.rmtree(settings.derived_dir / doc_id, ignore_errors=True)
