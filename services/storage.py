"""Safe local processing helpers and the asynchronous Cloudflare R2 adapter."""

import asyncio
import hashlib
import logging
import re
import time
import zipfile
from collections.abc import Mapping
from xml.etree import ElementTree
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol
from uuid import uuid4

import boto3
import filetype
from botocore.config import Config as BotoConfig
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from core.config import Settings, get_settings
from core.exceptions import (
    FileTooLargeError,
    InvalidMediaError,
    ObjectNotFoundError,
    ObjectStorageConfigurationError,
    ObjectStorageError,
    ObjectStoragePartialDeleteError,
    ObjectStorageProviderError,
    ObjectStorageTimeoutError,
    UnsupportedFileError,
)
from core.visitor import visitor_scope


logger = logging.getLogger("rag_multimodal.storage")


@dataclass(frozen=True)
class ObjectMetadata:
    """Provider-neutral metadata returned for one stored object."""

    key: str
    size_bytes: int
    content_type: str
    etag: str | None = None
    metadata: dict[str, str] | None = None


@dataclass(frozen=True)
class DeleteObjectsResult:
    """Safe result for one or more S3 multi-delete requests."""

    deleted_keys: tuple[str, ...] = ()
    failed_keys: tuple[str, ...] = ()


class ObjectStorage(Protocol):
    """Async contract for the private object store used by the application."""

    async def put_file(
        self, key: str, source: Path, *, content_type: str, metadata: Mapping[str, str] | None = None
    ) -> ObjectMetadata: ...

    async def put_stream(
        self, key: str, source: BinaryIO, *, content_type: str, metadata: Mapping[str, str] | None = None
    ) -> ObjectMetadata: ...

    async def head_object(self, key: str) -> ObjectMetadata | None: ...

    async def object_exists(self, key: str) -> bool: ...

    async def download_to_path(self, key: str, destination: Path) -> Path: ...

    async def delete_object(self, key: str) -> None: ...

    async def delete_objects(self, keys: list[str]) -> DeleteObjectsResult: ...

    async def list_objects_by_prefix(self, prefix: str) -> list[ObjectMetadata]: ...

    async def generate_presigned_get_url(self, key: str, *, expires_in: int | None = None) -> str: ...

    async def generate_presigned_put_url(
        self,
        key: str,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
        expires_in: int | None = None,
    ) -> str: ...

    async def health_check(self) -> bool: ...

    async def initialize(self) -> None: ...

    async def is_ready(self) -> bool: ...

    async def upload_object(
        self, key: str, source: Path, *, content_type: str, metadata: Mapping[str, str] | None = None
    ) -> ObjectMetadata: ...


_SAFE_METADATA_KEYS = {"doc-id", "visitor-id", "sha256", "original-name", "mime-type", "upload-version"}
_MAX_DOCX_ENTRIES = 2000
_MAX_DOCX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
_NOT_FOUND_CODES = {"404", "NoSuchKey", "NoSuchObject", "NotFound"}
_BUCKET_CODES = {"NoSuchBucket", "InvalidBucketName"}
_AUTH_CODES = {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "ExpiredToken", "InvalidToken"}


def normalize_object_key(key: str) -> str:
    """Normalize a relative S3 key and reject traversal or empty segments."""
    normalized = str(key or "").replace("\\", "/")
    parts = normalized.split("/")
    if not normalized or normalized.startswith("/") or any(not part or part in {".", ".."} for part in parts):
        raise InvalidMediaError("Storage key inválida")
    return "/".join(parts)


def _safe_segment(value: str, fallback: str) -> str:
    """Keep configuration and identifier segments path-safe."""
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._")
    return sanitized[:255] or fallback


def _safe_prefix(value: str) -> str:
    """Sanitize a configured prefix without allowing traversal."""
    return (
        "/".join(
            _safe_segment(part, "rag")
            for part in str(value).replace("\\", "/").split("/")
            if part not in {"", ".", ".."}
        )
        or "rag"
    )


def object_storage_base_prefix(settings: Settings | None = None) -> str:
    """Return the tenant-safe root for the configured environment and namespace."""
    settings = settings or get_settings()
    return normalize_object_key(
        f"{_safe_prefix(settings.r2_object_prefix)}/{_safe_segment(settings.app_env, 'development')}/{_safe_segment(settings.pinecone_namespace, 'default')}/documents"
    )


def document_object_prefix(doc_id: str, settings: Settings | None = None, visitor_id: str | None = None) -> str:
    """Return the isolated object prefix for one document UUID or opaque ID."""
    root = visitor_object_prefix(visitor_id, settings) if visitor_id else object_storage_base_prefix(settings)
    return f"{root}/{_safe_segment(doc_id, 'document')}"


def visitor_object_prefix(visitor_id: str, settings: Settings | None = None) -> str:
    """Return the backend-derived R2 prefix for exactly one visitor."""
    return f"{object_storage_base_prefix(settings)}/{visitor_scope(visitor_id)}"


def is_managed_object_key(key: str, settings: Settings | None = None, visitor_id: str | None = None, doc_id: str | None = None) -> bool:
    """Return whether a key belongs to the requested backend-derived R2 scope."""
    try:
        root = document_object_prefix(doc_id, settings, visitor_id) if doc_id else visitor_object_prefix(visitor_id, settings) if visitor_id else object_storage_base_prefix(settings)
        normalized = normalize_object_key(key)
        return normalized == root or normalized.startswith(f"{root}/")
    except InvalidMediaError:
        return False


def original_object_key(doc_id: str, filename: str, settings: Settings | None = None, visitor_id: str | None = None) -> str:
    """Build the private original-object key with a sanitized basename."""
    return f"{document_object_prefix(doc_id, settings, visitor_id)}/original/{sanitize_filename(filename)}"


def derived_object_key(doc_id: str, derived_name: str, settings: Settings | None = None, visitor_id: str | None = None) -> str:
    """Build the private derived-object key with a sanitized basename."""
    return f"{document_object_prefix(doc_id, settings, visitor_id)}/derived/{sanitize_filename(derived_name)}"


def original_object_metadata(
    doc_id: str, sha256: str, filename: str, mime_type: str, settings: Settings | None = None, visitor_id: str | None = None
) -> dict[str, str]:
    """Return the allowlisted, non-sensitive metadata for an original object."""
    settings = settings or get_settings()
    metadata = {
        "doc-id": _safe_segment(doc_id, "document"),
        "sha256": str(sha256)[:64],
        "original-name": sanitize_filename(filename),
        "mime-type": str(mime_type)[:255],
        "upload-version": str(settings.r2_upload_version)[:32],
    }
    if visitor_id:
        metadata["visitor-id"] = visitor_scope(visitor_id).removeprefix("visitor_")
    return metadata


class R2ObjectStorage:
    """Async facade over the synchronous boto3 S3-compatible R2 client."""

    def __init__(self, settings: Settings | None = None, *, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self.bucket = self.settings.r2_bucket_name
        self._client = client if client is not None else self._create_client()
        self._health_checked_at = 0.0
        self._health_state: bool | None = None

    async def put_file(
        self, key: str, source: Path, *, content_type: str, metadata: Mapping[str, str] | None = None
    ) -> ObjectMetadata:
        normalized, extra = self._upload_args(key, content_type, metadata)
        path = Path(source)
        if not path.is_file():
            raise ObjectStorageError("Arquivo temporário de upload não encontrado")
        await self._run("put_file", self._client.upload_file, str(path), self.bucket, normalized, ExtraArgs=extra)
        return ObjectMetadata(normalized, path.stat().st_size, content_type)

    async def put_stream(
        self, key: str, source: BinaryIO, *, content_type: str, metadata: Mapping[str, str] | None = None
    ) -> ObjectMetadata:
        normalized, extra = self._upload_args(key, content_type, metadata)
        await self._run("put_stream", self._client.upload_fileobj, source, self.bucket, normalized, ExtraArgs=extra)
        return ObjectMetadata(normalized, 0, content_type)

    async def head_object(self, key: str) -> ObjectMetadata | None:
        normalized = self._key(key)
        try:
            response = await self._run("head_object", self._client.head_object, Bucket=self.bucket, Key=normalized)
        except ObjectNotFoundError:
            return None
        return self._head_metadata(normalized, response)

    async def object_exists(self, key: str) -> bool:
        return (await self.head_object(key)) is not None

    async def download_to_path(self, key: str, destination: Path) -> Path:
        normalized = self._key(key)
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".r2-download-{uuid4().hex}"
        try:
            await self._run("download_to_path", self._client.download_file, self.bucket, normalized, str(temporary))
            if not temporary.is_file():
                raise ObjectStorageProviderError("O provedor não criou o arquivo solicitado")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    async def delete_object(self, key: str) -> None:
        normalized = self._key(key)
        response = await self._run("delete_object", self._client.delete_object, Bucket=self.bucket, Key=normalized)
        self._validate_response(response, "delete_object")

    async def delete_objects(self, keys: list[str]) -> DeleteObjectsResult:
        normalized = [self._key(key) for key in keys]
        deleted: list[str] = []
        failed: list[str] = []
        for start in range(0, len(normalized), 1000):
            batch = normalized[start : start + 1000]
            response = await self._run(
                "delete_objects",
                self._client.delete_objects,
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": False},
            )
            batch_deleted, batch_failed = self._delete_result(response, batch)
            deleted.extend(batch_deleted)
            failed.extend(batch_failed)
        result = DeleteObjectsResult(tuple(deleted), tuple(failed))
        if result.failed_keys:
            raise ObjectStoragePartialDeleteError(list(result.failed_keys), list(result.deleted_keys))
        return result

    async def list_objects_by_prefix(self, prefix: str) -> list[ObjectMetadata]:
        normalized = self._key(prefix).rstrip("/")
        objects: list[ObjectMetadata] = []
        token: str | None = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": f"{normalized}/", "MaxKeys": 1000}
            if token:
                kwargs["ContinuationToken"] = token
            response = await self._run("list_objects_by_prefix", self._client.list_objects_v2, **kwargs)
            objects.extend(self._list_metadata(response))
            if not response.get("IsTruncated"):
                return objects
            token = response.get("NextContinuationToken")
            if not isinstance(token, str) or not token:
                raise ObjectStorageProviderError("Resposta de paginação inválida do provedor")

    async def generate_presigned_get_url(self, key: str, *, expires_in: int | None = None) -> str:
        return await self._presigned("get_object", key, expires_in)

    async def generate_presigned_put_url(
        self,
        key: str,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
        expires_in: int | None = None,
    ) -> str:
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": self._key(key)}
        if content_type:
            params["ContentType"] = self._content_type(content_type)
        if metadata:
            params["Metadata"] = {
                name.lower(): str(value) for name, value in metadata.items() if name.lower() in _SAFE_METADATA_KEYS
            }
        return await self._presigned("put_object", key, expires_in, params=params)

    async def health_check(self) -> bool:
        now = time.monotonic()
        if self._health_state is not None and now - self._health_checked_at < self.settings.r2_health_cache_seconds:
            return self._health_state
        try:
            await self._run("health_check", self._client.head_bucket, Bucket=self.bucket)
        except Exception:
            self._health_state = False
        else:
            self._health_state = True
        self._health_checked_at = time.monotonic()
        return self._health_state

    async def initialize(self) -> None:
        await self.health_check()

    async def is_ready(self) -> bool:
        return await self.health_check()

    async def upload_object(
        self, key: str, source: Path, *, content_type: str, metadata: Mapping[str, str] | None = None
    ) -> ObjectMetadata:
        return await self.put_file(key, source, content_type=content_type, metadata=metadata)

    async def download_object(self, key: str, destination: Path) -> Path:
        return await self.download_to_path(key, destination)

    async def get_metadata(self, key: str) -> ObjectMetadata | None:
        return await self.head_object(key)

    async def generate_presigned_url(self, key: str, *, ttl_seconds: int | None = None) -> str:
        return await self.generate_presigned_get_url(key, expires_in=ttl_seconds)

    def generate_presigned_get_url_sync(self, key: str, *, expires_in: int | None = None) -> str:
        return self._presigned_sync("get_object", key, expires_in)

    def _create_client(self) -> Any:
        self._validate_configuration()
        config = BotoConfig(
            signature_version="s3v4",
            connect_timeout=self.settings.r2_connect_timeout_seconds,
            read_timeout=self.settings.r2_read_timeout_seconds,
            retries={"max_attempts": self.settings.r2_max_attempts, "mode": "standard"},
            s3={"addressing_style": "path"},
        )
        return boto3.client(
            "s3",
            endpoint_url=self.settings.r2_endpoint_url,
            region_name="auto",
            aws_access_key_id=self.settings.r2_access_key_id,
            aws_secret_access_key=self.settings.r2_secret_access_key,
            config=config,
        )

    def _validate_configuration(self) -> None:
        missing = [
            name
            for name, value in (
                ("R2_ENDPOINT_URL", self.settings.r2_endpoint_url),
                ("R2_ACCESS_KEY_ID", self.settings.r2_access_key_id),
                ("R2_SECRET_ACCESS_KEY", self.settings.r2_secret_access_key),
                ("R2_BUCKET_NAME", self.settings.r2_bucket_name),
            )
            if not str(value).strip()
        ]
        if missing:
            raise ObjectStorageConfigurationError(f"Configuração R2 ausente: {', '.join(missing)}")

    def _key(self, key: str) -> str:
        normalized = normalize_object_key(key)
        base = object_storage_base_prefix(self.settings)
        if normalized != base and not normalized.startswith(f"{base}/"):
            raise InvalidMediaError("Storage key fora do namespace configurado")
        return normalized

    def _upload_args(
        self, key: str, content_type: str, metadata: Mapping[str, str] | None
    ) -> tuple[str, dict[str, Any]]:
        normalized = self._key(key)
        extra: dict[str, Any] = {"ContentType": self._content_type(content_type)}
        safe_metadata = {
            name: str(value)[:1024] for name, value in (metadata or {}).items() if name.lower() in _SAFE_METADATA_KEYS
        }
        if safe_metadata:
            extra["Metadata"] = safe_metadata
        return normalized, extra

    def _content_type(self, content_type: str) -> str:
        value = str(content_type or "").strip()
        if not value or len(value) > 255:
            raise ObjectStorageProviderError("MIME type inválido para o objeto")
        return value

    async def _run(self, operation: str, function: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except Exception as error:
            raise self._translate_error(error, operation) from None

    async def _presigned(
        self, operation: str, key: str, expires_in: int | None, *, params: dict[str, Any] | None = None
    ) -> str:
        return await asyncio.to_thread(self._presigned_sync, operation, key, expires_in, params=params)

    def _presigned_sync(
        self, operation: str, key: str, expires_in: int | None, *, params: dict[str, Any] | None = None
    ) -> str:
        ttl = expires_in or self.settings.r2_presigned_url_ttl_seconds
        if ttl <= 0 or ttl > 3600:
            raise ObjectStorageProviderError("Expiração de URL pré-assinada inválida")
        parameters = params or {"Bucket": self.bucket, "Key": self._key(key)}
        try:
            url = self._client.generate_presigned_url(ClientMethod=operation, Params=parameters, ExpiresIn=ttl)
        except Exception as error:
            raise self._translate_error(error, operation) from None
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ObjectStorageProviderError("Resposta de URL pré-assinada inválida")
        return url

    def _head_metadata(self, key: str, response: Any) -> ObjectMetadata:
        self._validate_response(response, "head_object")
        if (
            not isinstance(response, Mapping)
            or not isinstance(response.get("ContentLength"), int)
            or response["ContentLength"] < 0
        ):
            raise ObjectStorageProviderError("Resposta HEAD inválida do provedor")
        content_type = response.get("ContentType") or "application/octet-stream"
        if not isinstance(content_type, str):
            raise ObjectStorageProviderError("MIME inválido na resposta HEAD")
        etag = response.get("ETag")
        metadata = {str(name).lower(): str(value) for name, value in (response.get("Metadata") or {}).items()}
        return ObjectMetadata(
            key, response["ContentLength"], content_type, etag if isinstance(etag, str) else None, metadata
        )

    def _list_metadata(self, response: Any) -> list[ObjectMetadata]:
        self._validate_response(response, "list_objects_by_prefix")
        contents = response.get("Contents", [])
        if not isinstance(contents, list):
            raise ObjectStorageProviderError("Resposta LIST inválida do provedor")
        result: list[ObjectMetadata] = []
        for item in contents:
            if (
                not isinstance(item, Mapping)
                or not isinstance(item.get("Key"), str)
                or not isinstance(item.get("Size"), int)
                or item["Size"] < 0
            ):
                raise ObjectStorageProviderError("Objeto inválido na resposta LIST")
            result.append(
                ObjectMetadata(
                    item["Key"],
                    item["Size"],
                    "application/octet-stream",
                    item.get("ETag") if isinstance(item.get("ETag"), str) else None,
                )
            )
        return result

    def _delete_result(self, response: Any, requested: list[str]) -> tuple[list[str], list[str]]:
        self._validate_response(response, "delete_objects")
        deleted = self._response_keys(response, "Deleted")
        errors = self._response_keys(response, "Errors")
        reported = set(deleted) | set(errors)
        missing = [key for key in requested if key not in reported]
        return deleted, errors + missing

    def _response_keys(self, response: Mapping[str, Any], field: str) -> list[str]:
        values = response.get(field, [])
        if not isinstance(values, list):
            raise ObjectStorageProviderError(f"Resposta {field} inválida do provedor")
        keys: list[str] = []
        for item in values:
            if not isinstance(item, Mapping) or not isinstance(item.get("Key"), str):
                raise ObjectStorageProviderError(f"Chave inválida na resposta {field}")
            keys.append(item["Key"])
        return keys

    def _validate_response(self, response: Any, operation: str) -> None:
        if response is not None and not isinstance(response, Mapping):
            raise ObjectStorageProviderError(f"Resposta inválida em {operation}")

    def _translate_error(self, error: Exception, operation: str) -> ObjectStorageError:
        if isinstance(error, ObjectStorageError):
            return error
        code = self._error_code(error)
        if code in _NOT_FOUND_CODES:
            return ObjectNotFoundError("Objeto não encontrado no storage")
        if code in _BUCKET_CODES:
            return ObjectStorageError("Bucket R2 não encontrado")
        if code in _AUTH_CODES:
            return ObjectStorageConfigurationError("Credenciais R2 inválidas ou sem permissão")
        if isinstance(error, (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError, TimeoutError)):
            return ObjectStorageTimeoutError("Tempo limite excedido no storage")
        if isinstance(error, (ClientError, BotoCoreError)):
            return ObjectStorageError(f"Falha do provedor R2 em {operation}")
        return ObjectStorageError(f"Falha inesperada no storage em {operation}")

    def _error_code(self, error: Exception) -> str:
        if not isinstance(error, ClientError) or not isinstance(error.response, Mapping):
            return error.__class__.__name__
        details = error.response.get("Error", {})
        return str(details.get("Code", "")) if isinstance(details, Mapping) else ""


_storage_cache: dict[int, R2ObjectStorage] = {}


def get_object_storage(settings: Settings | None = None) -> R2ObjectStorage:
    """Return one cached backend per settings object, preserving health caching."""
    settings = settings or get_settings()
    cache_key = id(settings)
    if cache_key not in _storage_cache:
        _storage_cache[cache_key] = R2ObjectStorage(settings)
    return _storage_cache[cache_key]


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
    basename = str(filename or "upload").replace("\\", "/").rsplit("/", 1)[-1]
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


def validate_signature(path: Path, extension: str) -> None:
    """Validate the real file signature without loading the whole file."""
    extension = extension.lower()
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
    if extension in IMAGE_EXTENSIONS and known_mime != MIME_MAP[extension]:
        raise UnsupportedFileError("Assinatura de imagem inválida")
    if extension in AUDIO_EXTENSIONS and not _is_audio_signature(header, known_mime, extension):
        raise UnsupportedFileError("Assinatura de áudio inválida")
    if extension in VIDEO_EXTENSIONS and not (b"ftyp" in header[:64] or known_mime == MIME_MAP[extension]):
        raise UnsupportedFileError("Assinatura de vídeo inválida")


def _validate_text_header(header: bytes) -> None:
    """Reject a text upload whose sampled bytes are not UTF-8."""
    try:
        text = header.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UnsupportedFileError("Arquivo de texto não é UTF-8 válido") from error
    if any(ord(character) < 32 and character not in "\t\n\f\r" for character in text):
        raise UnsupportedFileError("Assinatura de texto inválida")


def _is_audio_signature(header: bytes, known_mime: str, extension: str) -> bool:
    """Accept recognized audio signatures and the standard minimal headers."""
    if extension == ".mp3":
        return known_mime == "audio/mpeg" or header.startswith(b"ID3")
    return known_mime in {"audio/wav", "audio/x-wav"} or header[:12].startswith(b"RIFF") and header[8:12] == b"WAVE"


def _validate_docx_package(path: Path) -> None:
    """Require the two structural entries that identify a DOCX package."""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_DOCX_ENTRIES or sum(info.file_size for info in infos) > _MAX_DOCX_UNCOMPRESSED_BYTES:
                raise UnsupportedFileError("Pacote DOCX excede o limite de expansÃ£o")
            required = {"[Content_Types].xml", "word/document.xml"}
            infos = {name: archive.getinfo(name) for name in required}
            if any(info.is_dir() for info in infos.values()):
                raise UnsupportedFileError("Assinatura DOCX inválida")
            for name in required:
                with archive.open(infos[name]) as member:
                    ElementTree.parse(member)
    except (ElementTree.ParseError, KeyError, OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise UnsupportedFileError("Assinatura DOCX inválida") from error


def _temporary_path(settings: Settings, prefix: str, directory: Path | None = None) -> Path:
    """Create a unique path inside the controlled storage directory."""
    root = directory or settings.temp_processing_dir
    root.mkdir(parents=True, exist_ok=True)
    return root / f".{prefix}-{uuid4().hex}"


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
    if not (destination.exists() or destination.is_symlink()):
        return None
    if destination.is_symlink():
        _reject_existing_destination("symlink")
    try:
        valid_integrity = (
            destination.is_file() and destination.stat().st_size == size and sha256_for_path(destination) == sha256
        )
    except OSError as error:
        _reject_existing_destination("stat_or_hash_error", error)
    if not valid_integrity:
        _reject_existing_destination("integrity_mismatch")
    try:
        validate_signature(destination, extension)
    except Exception as error:
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
        destination = settings.temp_processing_dir / f"{sha256}_{safe_name}"
        validate_signature(temporary, extension)
        reused = _reuse_existing(destination, temporary, sha256, size, extension)
        if reused:
            return reused
        temporary.replace(destination)
        return StoredUpload(destination, sha256, destination.name, size)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

def stage_existing_file(
    path: Path,
    settings: Settings | None = None,
    *,
    destination_dir: Path | None = None,
    temporary_dir: Path | None = None,
) -> StoredUpload:
    """Copy a CLI file into controlled temporary storage without loading it in memory."""
    settings = settings or get_settings()
    source = path.resolve()
    if not source.is_file():
        raise InvalidMediaError("Arquivo de entrada não encontrado")
    safe_name = sanitize_filename(source.name)
    extension = extension_for(safe_name)
    destination_root = destination_dir or settings.temp_processing_dir / "inputs"
    temporary = _temporary_path(settings, "staging", temporary_dir or destination_root)
    try:
        sha256, size = _copy_path(source, temporary, settings.max_upload_size_bytes)
        destination = destination_root / f"{sha256}_{safe_name}"
        destination_root.mkdir(parents=True, exist_ok=True)
        validate_signature(temporary, extension)
        reused = _reuse_existing(destination, temporary, sha256, size, extension)
        if reused:
            return reused
        temporary.replace(destination)
        return StoredUpload(destination, sha256, destination.name, size)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
