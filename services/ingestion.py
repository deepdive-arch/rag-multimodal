"""End-to-end ingestion with Postgres state, R2 objects and idempotent indexing."""

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.config import Settings, get_settings
from core.exceptions import AppError, IngestionError, InvalidMediaError
from db.catalog import Catalog, utc_now
from services.embeddings import embed_media, embed_text, prepare_document_text
from services.media import MediaItem, extract_items
from services.deletion import (
    clear_namespace,
    clear_namespace_async,
    delete_document as coordinated_delete_document,
    delete_document_async,
)
from services.pinecone_service import delete_vectors, upsert_vectors
from services.storage import (
    derived_object_key,
    document_object_prefix,
    extension_for,
    file_type_for,
    get_object_storage,
    mime_for,
    original_object_key,
    original_object_metadata,
    sanitize_filename,
    sha256_for_path,
    stage_existing_file,
    validate_signature,
)


logger = logging.getLogger("rag_multimodal.ingestion")


@dataclass(frozen=True)
class IngestionResult:
    """Public ingestion result."""

    doc_id: str
    name: str
    file_type: str
    chunks: int
    duplicate: bool
    warnings: list[str]


def ingest_file(
    path: str | Path,
    original_name: str | None = None,
    *,
    force: bool = False,
    quota_client_hash: str | None = None,
    quota_usage_date: date | None = None,
    visitor_id: str | None = None,
) -> IngestionResult:
    """Run one complete ingestion in one async worker loop."""
    return asyncio.run(_ingest_file(Path(path), original_name, force=force, quota_client_hash=quota_client_hash, quota_usage_date=quota_usage_date, visitor_id=visitor_id))


def process_uploaded_document(doc_id: str, settings: Settings | None = None) -> IngestionResult | None:
    """Process one object already uploaded to R2 without leaking background errors."""
    try:
        return asyncio.run(_process_uploaded_document(doc_id, settings or get_settings()))
    except AppError as error:
        logger.warning("direct_upload_processing_failed", extra={"doc_id": doc_id, "error_type": type(error).__name__})
    except Exception as error:
        logger.error("direct_upload_processing_unexpected_failure", extra={"doc_id": doc_id, "error_type": type(error).__name__})
    return None


def retry_document(doc_id: str, settings: Settings | None = None) -> IngestionResult | None:
    """Retry a failed document after an atomic Postgres claim."""
    try:
        return asyncio.run(_retry_document(doc_id, settings or get_settings()))
    except AppError as error:
        logger.warning("document_retry_failed", extra={"doc_id": doc_id, "error_type": type(error).__name__})
    except Exception as error:
        logger.error("document_retry_unexpected_failure", extra={"doc_id": doc_id, "error_type": type(error).__name__})
    return None


async def _process_uploaded_document(doc_id: str, settings: Settings) -> IngestionResult | None:
    """Download one authorized R2 object into an isolated disposable workspace."""
    catalog = Catalog.ephemeral(settings)
    workspace = settings.temp_processing_dir / sanitize_filename(doc_id)
    process_called = False
    visitor_id = None
    try:
        record = await catalog.get_file(doc_id)
        if not record or record["status"] != "processing":
            return None
        visitor_id = record.get("visitor_id")
        candidate = workspace / "original" / record["sanitized_name"]
        await get_object_storage(settings).download_to_path(record["storage_key"], candidate)
        _validate_downloaded_original(candidate, record)
        process_called = True
        return await _process_document(catalog, candidate, record, doc_id, settings)
    except AppError as error:
        if not process_called:
            await _fail_before_processing(catalog, doc_id, settings, error, keep_original=True, visitor_id=visitor_id)
        raise
    except Exception as error:
        if not process_called:
            await _fail_before_processing(catalog, doc_id, settings, error, keep_original=True, visitor_id=visitor_id)
        raise IngestionError("Falha inesperada durante a ingestão.") from error
    finally:
        try:
            await catalog.close()
        finally:
            shutil.rmtree(workspace, ignore_errors=True)


async def _retry_document(doc_id: str, settings: Settings) -> IngestionResult | None:
    """Claim failed work and reprocess the durable original stored in R2."""
    catalog = Catalog.ephemeral(settings)
    workspace = settings.temp_processing_dir / sanitize_filename(doc_id)
    process_called = False
    visitor_id = None
    try:
        record = await catalog.get_file(doc_id)
        if not record or record["status"] != "failed" or not await catalog.claim_retry(doc_id):
            return None
        visitor_id = record.get("visitor_id")
        await _delete_derived_objects(doc_id, settings, visitor_id)
        vector_ids = await catalog.get_vector_ids(doc_id)
        await asyncio.to_thread(delete_vectors, vector_ids, settings, visitor_id)
        await catalog.clear_processing_artifacts(doc_id)
        candidate = workspace / "original" / record["sanitized_name"]
        await get_object_storage(settings).download_to_path(record["storage_key"], candidate)
        _validate_downloaded_original(candidate, record)
        process_called = True
        return await _process_document(catalog, candidate, record, doc_id, settings)
    except AppError as error:
        if not process_called:
            await _fail_before_processing(
                catalog, doc_id, settings, error, keep_original=True, visitor_id=visitor_id
            )
        raise
    except Exception as error:
        if not process_called:
            await _fail_before_processing(catalog, doc_id, settings, error, keep_original=True, visitor_id=visitor_id)
        raise IngestionError("Falha inesperada durante a ingestão.") from error
    finally:
        try:
            await catalog.close()
        finally:
            shutil.rmtree(workspace, ignore_errors=True)


def _validate_downloaded_original(candidate: Path, record: dict[str, Any]) -> None:
    """Recompute every original invariant before extraction trusts the object."""
    expected_mime = mime_for(record["sanitized_name"])
    if candidate.stat().st_size != record["size_bytes"]:
        raise InvalidMediaError("O tamanho do objeto não corresponde ao cadastro")
    if sha256_for_path(candidate) != record["sha256"]:
        raise InvalidMediaError("O conteúdo do objeto não corresponde ao SHA-256 declarado")
    if expected_mime != record["mime_type"] or mime_for(candidate) != expected_mime:
        raise InvalidMediaError("O MIME do objeto não corresponde ao cadastro")
    validate_signature(candidate, extension_for(record["sanitized_name"]))


async def _ingest_file(
    path: Path,
    original_name: str | None,
    *,
    force: bool,
    quota_client_hash: str | None,
    quota_usage_date: date | None,
    visitor_id: str | None,
) -> IngestionResult:
    """Create or claim a document, then process only its R2-backed original."""
    settings = get_settings()
    source = path.resolve()
    if not source.is_file():
        raise InvalidMediaError("Arquivo de entrada não encontrado")
    sha256 = sha256_for_path(source)
    name = original_name or source.name
    catalog = Catalog.ephemeral(settings)
    doc_id: str | None = None
    process_called = False
    try:
        existing = await catalog.find_by_sha256(sha256, visitor_id)
        if existing:
            return await _handle_existing(catalog, existing, source, name, force, settings)
        record = _upload_record(source, name, sha256, settings, visitor_id)
        created = await _create_ingest_document(catalog, record, quota_client_hash, quota_usage_date)
        if not created.get("created"):
            return await _handle_existing(catalog, created, source, name, force, settings)
        doc_id = created["doc_id"]
        workspace = settings.temp_processing_dir / sanitize_filename(doc_id)
        candidate = _stage_in_workspace(source, settings, workspace)
        _validate_downloaded_original(candidate, record)
        await _upload_original(get_object_storage(settings), candidate, record, doc_id, settings)
        await catalog.update_status(doc_id, "uploaded")
        if not await catalog.claim_processing(doc_id):
            return _duplicate_result(await catalog.get_file(doc_id) or record)
        record = await catalog.get_file(doc_id) or record
        downloaded = workspace / "downloaded" / record["sanitized_name"]
        await get_object_storage(settings).download_to_path(record["storage_key"], downloaded)
        _validate_downloaded_original(downloaded, record)
        process_called = True
        return await _process_document(catalog, downloaded, record, doc_id, settings)
    except AppError as error:
        if doc_id and not process_called:
            await _fail_before_processing(catalog, doc_id, settings, error, keep_original=False, visitor_id=visitor_id)
        raise
    except Exception as error:
        if doc_id and not process_called:
            await _fail_before_processing(catalog, doc_id, settings, error, keep_original=False, visitor_id=visitor_id)
        raise IngestionError("Falha inesperada durante a ingestão.") from error
    finally:
        try:
            await catalog.close()
        finally:
            if doc_id:
                shutil.rmtree(settings.temp_processing_dir / sanitize_filename(doc_id), ignore_errors=True)


async def _handle_existing(
    catalog: Catalog, existing: dict[str, Any], source: Path, name: str, force: bool, settings: Settings
) -> IngestionResult:
    """Apply the content-hash idempotency policy under the database unique key."""
    status = existing["status"]
    if status == "ready":
        return _duplicate_result(existing)
    if status in {"pending_upload", "uploaded", "processing", "indexing", "deleting"}:
        return _duplicate_result(existing)
    if not force:
        raise IngestionError("O documento falhou; solicite um reprocessamento explícito.")
    if not await catalog.claim_retry(existing["doc_id"]):
        return _duplicate_result(await catalog.get_file(existing["doc_id"]) or existing)
    doc_id = existing["doc_id"]
    workspace = settings.temp_processing_dir / sanitize_filename(doc_id)
    process_called = False
    try:
        await _delete_derived_objects(doc_id, settings, existing.get("visitor_id"))
        vector_ids = await catalog.get_vector_ids(doc_id)
        await asyncio.to_thread(delete_vectors, vector_ids, settings, existing.get("visitor_id"))
        await catalog.clear_processing_artifacts(doc_id)
        candidate = _stage_in_workspace(source, settings, workspace)
        record = await catalog.get_file(doc_id) or existing
        _validate_downloaded_original(candidate, record)
        await _upload_original(get_object_storage(settings), candidate, record, doc_id, settings)
        downloaded = workspace / "downloaded" / record["sanitized_name"]
        await get_object_storage(settings).download_to_path(record["storage_key"], downloaded)
        _validate_downloaded_original(downloaded, record)
        process_called = True
        return await _process_document(catalog, downloaded, record, doc_id, settings)
    except AppError as error:
        if not process_called:
            await _fail_before_processing(catalog, doc_id, settings, error, keep_original=True, visitor_id=existing.get("visitor_id"))
        raise
    except Exception as error:
        if not process_called:
            await _fail_before_processing(catalog, doc_id, settings, error, keep_original=True, visitor_id=existing.get("visitor_id"))
        raise IngestionError("Falha inesperada durante a ingestão.") from error
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


async def _create_ingest_document(catalog: Catalog, record: dict[str, Any], client_hash: str | None, usage_date: date | None) -> dict[str, Any]:
    """Use the atomic public reservation only when the caller supplied identity."""
    if client_hash and usage_date:
        return await catalog.create_document_with_quota(record, client_hash, usage_date)
    return await catalog.create_document(record)


async def _process_document(
    catalog: Catalog,
    candidate: Path,
    record: dict[str, Any],
    doc_id: str,
    settings: Settings,
) -> IngestionResult:
    """Extract, persist media, embed, index and commit one ready document."""
    vector_ids: list[str] = []
    visitor_id = record.get("visitor_id")
    try:
        _validate_downloaded_original(candidate, record)
        storage = get_object_storage(settings)
        items = extract_items(candidate, doc_id, settings)
        if not items:
            raise InvalidMediaError("O arquivo não contém conteúdo indexável.")
        derived = await _upload_derived(storage, items, candidate, doc_id, settings, visitor_id)
        object_ids = await catalog.register_objects(derived)
        object_ids.update({record["object_key"]: record.get("object_id", "")})
        vectors, rows, warnings = _build_vectors(items, record, candidate, doc_id, settings, object_ids, visitor_id)
        if not rows:
            raise InvalidMediaError("O arquivo não contém conteúdo indexável.")
        vector_ids = [row["chunk_id"] for row in rows]
        await catalog.update_status(doc_id, "indexing")
        await asyncio.to_thread(upsert_vectors, vectors, settings, visitor_id)
        await catalog.register_chunks(rows)
        await catalog.update_status(doc_id, "ready", chunks_count=len(rows), warnings=warnings)
        return IngestionResult(doc_id, record["original_name"], record["file_type"], len(rows), False, warnings)
    except AppError as error:
        await _rollback(catalog, doc_id, vector_ids, settings, error, visitor_id)
        raise
    except Exception as error:
        await _rollback(catalog, doc_id, vector_ids, settings, error, visitor_id)
        raise IngestionError("Falha inesperada durante a ingestão.") from error
    finally:
        shutil.rmtree(settings.temp_processing_dir / sanitize_filename(doc_id) / "derived", ignore_errors=True)


async def _upload_original(
    storage: Any, candidate: Path, record: dict[str, Any], doc_id: str, settings: Settings
) -> None:
    """Persist the validated original in the document's private R2 prefix."""
    await storage.put_file(
        record["object_key"],
        candidate,
        content_type=record["mime_type"],
        metadata=original_object_metadata(
            doc_id, record["sha256"], record["sanitized_name"], record["mime_type"], settings, record.get("visitor_id")
        ),
    )


def _stage_in_workspace(source: Path, settings: Settings, workspace: Path) -> Path:
    """Copy an input into an isolated directory that is always disposable."""
    return stage_existing_file(source, settings, destination_dir=workspace / "input", temporary_dir=workspace).path


def _upload_record(candidate: Path, name: str, sha256: str, settings: Settings, visitor_id: str | None = None) -> dict[str, Any]:
    """Build a pending catalog record without storing local paths."""
    doc_id = str(uuid4())
    sanitized = sanitize_filename(name or candidate.name)
    return {
        "doc_id": doc_id,
        "original_name": name or sanitized,
        "sanitized_name": sanitized,
        "object_key": original_object_key(doc_id, sanitized, settings, visitor_id),
        "file_type": file_type_for(candidate),
        "mime_type": mime_for(candidate),
        "sha256": sha256,
        "size_bytes": candidate.stat().st_size,
        "status": "pending_upload",
        "created_at": utc_now(),
        "visitor_id": visitor_id,
    }


def _duplicate_result(record: dict[str, Any]) -> IngestionResult:
    """Map an existing document to the CLI/API result."""
    return IngestionResult(
        record["doc_id"], record["original_name"], record["file_type"], record["chunks_count"], True, _warnings(record)
    )


def _build_vectors(
    items: list[MediaItem],
    record: dict[str, Any],
    candidate: Path,
    doc_id: str,
    settings: Settings,
    object_ids: dict[str, str],
    visitor_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Embed items and build minimal Pinecone metadata plus Postgres references."""
    vectors: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    warnings = sorted({warning for item in items for warning in item.warnings})
    for index, item in enumerate(items):
        chunk_id = f"{doc_id}-chunk-{index}"
        vector = (
            embed_text(prepare_document_text(item.text, record["original_name"]), settings)
            if item.content_modality == "text"
            else embed_media(item.media_path.read_bytes(), item.mime_type, settings)
        )  # type: ignore[union-attr]
        media_key = _media_key(item, candidate, record, doc_id, settings, visitor_id)
        metadata = {
            "doc_id": doc_id,
            "chunk_id": chunk_id,
            "file_name": record["original_name"],
            "original_name": record["original_name"],
            "file_type": item.file_type,
            "mime_type": item.mime_type or record["mime_type"],
            "content_modality": item.content_modality,
            "page": item.page_number,
            "page_number": item.page_number,
            "chunk_text": item.text,
            "text_preview": item.text[: settings.text_preview_size],
            "object_key": media_key,
            "media_key": media_key,
            "duration_seconds": item.duration_seconds,
        }
        if visitor_id:
            metadata["visitor_id"] = visitor_id
        vectors.append({"id": chunk_id, "values": vector, "metadata": metadata})
        rows.append(
            {
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "chunk_index": index,
                "page_number": item.page_number,
                "content_modality": item.content_modality,
                "media_key": media_key,
                "object_id": object_ids.get(media_key) or None,
                "mime_type": item.mime_type or record["mime_type"],
                "size_bytes": item.media_path.stat().st_size if item.media_path and item.media_path.is_file() else 0,
            }
        )
    return vectors, rows, warnings


async def _upload_derived(
    storage: Any, items: list[MediaItem], candidate: Path, doc_id: str, settings: Settings, visitor_id: str | None = None
) -> list[dict[str, Any]]:
    """Upload each generated media file and return durable object metadata."""
    uploaded: set[str] = set()
    objects: list[dict[str, Any]] = []
    for item in items:
        if not item.media_path or item.media_path.resolve() == candidate.resolve():
            continue
        key = derived_object_key(doc_id, item.media_path.name, settings, visitor_id)
        if key in uploaded:
            continue
        await storage.put_file(key, item.media_path, content_type=item.mime_type or mime_for(item.media_path))
        uploaded.add(key)
        objects.append(
            {
                "doc_id": doc_id,
                "object_key": key,
                "mime_type": item.mime_type or mime_for(item.media_path),
                "size_bytes": item.media_path.stat().st_size,
                "sha256": sha256_for_path(item.media_path),
                "page_number": item.page_number,
            }
        )
    return objects


async def _rollback(
    catalog: Catalog, doc_id: str, vector_ids: list[str], settings: Settings, error: Exception | str, visitor_id: str | None = None
) -> None:
    """Best-effort cleanup while retaining a retryable failed document."""
    vectors_clean = True
    storage_clean = True
    try:
        await asyncio.to_thread(delete_vectors, vector_ids, settings, visitor_id)
    except Exception:
        vectors_clean = False
        logger.warning("ingestion_rollback_vectors_failed", extra={"doc_id": doc_id})
    try:
        await _delete_derived_objects(doc_id, settings, visitor_id)
    except Exception:
        storage_clean = False
        logger.warning("ingestion_rollback_storage_failed", extra={"doc_id": doc_id})
    try:
        if vectors_clean and storage_clean:
            await catalog.clear_processing_artifacts(doc_id)
        warnings = [] if vectors_clean and storage_clean else ["cleanup_pending_reconciliation"]
        await catalog.update_status(doc_id, "failed", chunks_count=len(vector_ids), warnings=warnings, error_message=_safe_failure_message(error))
    except Exception:
        logger.warning("ingestion_rollback_catalog_failed", extra={"doc_id": doc_id})


async def _fail_before_processing(
    catalog: Catalog, doc_id: str, settings: Settings, error: Exception | str, *, keep_original: bool, visitor_id: str | None = None
) -> None:
    """Mark pre-index failures safely and remove uncertain external uploads."""
    try:
        if keep_original:
            await _delete_derived_objects(doc_id, settings, visitor_id)
        else:
            await _remove_r2_document_objects(doc_id, settings, visitor_id)
    except Exception:
        logger.warning("ingestion_preprocessing_storage_cleanup_failed", extra={"doc_id": doc_id})
    try:
        await catalog.update_status(doc_id, "failed", warnings=[], error_message=_safe_failure_message(error))
    except Exception:
        logger.warning("ingestion_preprocessing_catalog_failed", extra={"doc_id": doc_id})


def _safe_failure_message(error: Exception | str) -> str:
    """Keep technical provider details out of durable catalog data."""
    if isinstance(error, AppError):
        return str(error)
    return "Falha inesperada durante a ingestão."


def _warnings(record: dict[str, Any]) -> list[str]:
    """Decode catalog warnings safely."""
    value = record.get("warnings", record.get("warnings_json", []))
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
        return decoded if isinstance(decoded, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


async def _delete_derived_objects(doc_id: str, settings: Settings, visitor_id: str | None = None) -> None:
    """Delete only generated media so the durable original remains retryable."""
    await _remove_r2_prefix(f"{document_object_prefix(doc_id, settings, visitor_id)}/derived", settings)


async def _remove_r2_document_objects(doc_id: str, settings: Settings, visitor_id: str | None = None) -> None:
    """Delete every original and derived object under one document prefix."""
    await _remove_r2_prefix(document_object_prefix(doc_id, settings, visitor_id), settings)


async def _remove_r2_prefix(prefix: str, settings: Settings) -> None:
    """List then delete a bounded document or namespace prefix."""
    storage = get_object_storage(settings)
    objects = await storage.list_objects_by_prefix(prefix)
    if objects:
        await storage.delete_objects([item.key for item in objects])


def _media_key(item: MediaItem, candidate: Path, record: dict[str, Any], doc_id: str, settings: Settings, visitor_id: str | None = None) -> str:
    """Map original media to its original object and generated media to R2."""
    if not item.media_path:
        return ""
    return (
        record["object_key"]
        if item.media_path.resolve() == candidate.resolve()
        else derived_object_key(doc_id, item.media_path.name, settings, visitor_id)
    )


def _is_relative_to(candidate: Path, root: Path) -> bool:
    """Return whether a resolved path is inside a controlled root."""
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


async def _delete_document(doc_id: str, settings: Settings):
    """Compatibility entry point for the retryable deletion coordinator."""
    return await delete_document_async(doc_id, settings)


def delete_document(doc_id: str, settings: Settings | None = None):
    """Keep the synchronous service signature used by the API worker."""
    return coordinated_delete_document(doc_id, settings or get_settings())


async def _clear_index(settings: Settings, confirmation: str, admin_token: str | None):
    """Compatibility entry point for guarded namespace cleanup."""
    return await clear_namespace_async(settings, confirmation, admin_token)


def clear_index(
    settings: Settings | None = None, *, confirmation: str | None = None, admin_token: str | None = None
):
    """Keep the synchronous destructive service signature."""
    return clear_namespace(settings or get_settings(), confirmation=confirmation, admin_token=admin_token)
