"""End-to-end ingestion, rollback and destructive catalog operations."""

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.config import Settings, get_settings
from core.exceptions import AppError, FileNotFoundInCatalogError, IngestionError, InvalidMediaError
from db.catalog import Catalog, utc_now
from services.embeddings import embed_media, embed_text, prepare_document_text
from services.media import MediaItem, extract_items
from services.pinecone_service import delete_all_vectors, delete_vectors, upsert_vectors
from services.storage import file_type_for, mime_for, remove_storage, sha256_for_path, stage_existing_file, storage_key_for_path


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


def ingest_file(path: str | Path, original_name: str | None = None, *, force: bool = False) -> IngestionResult:
    """Ingest one file into local storage, Gemini and Pinecone."""
    settings = get_settings()
    candidate = _permanent_path(Path(path), settings)
    doc_id = sha256_for_path(candidate)
    name = original_name or candidate.name.split("_", 1)[-1]
    catalog = Catalog(settings.database_path)
    _run(catalog.initialize())
    existing = _run(catalog.get_file(doc_id))
    if existing and existing["status"] == "ready" and not force:
        if candidate.name != existing["stored_name"]:
            candidate.unlink(missing_ok=True)
        return IngestionResult(doc_id, existing["original_name"], existing["file_type"], existing["chunks_count"], True, _warnings(existing))
    if existing and force:
        delete_vectors(_run(catalog.get_vector_ids(doc_id)), settings)
        _run(catalog.delete_chunks(doc_id))
    file_type = file_type_for(candidate)
    mime_type = mime_for(candidate)
    record = {"doc_id": doc_id, "original_name": name, "stored_name": candidate.name, "storage_key": storage_key_for_path(candidate, settings), "file_type": file_type, "mime_type": mime_type, "size_bytes": candidate.stat().st_size, "created_at": utc_now()}
    _run(catalog.create_processing_file(record))
    created_vectors: list[str] = []
    try:
        items = extract_items(candidate, doc_id, settings)
        if not items:
            raise InvalidMediaError("O arquivo não contém conteúdo indexável.")
        vectors, rows, warnings = _build_vectors(items, record, doc_id, settings)
        if not rows:
            raise InvalidMediaError("O arquivo não contém conteúdo indexável.")
        created_vectors = [row["vector_id"] for row in rows]
        upsert_vectors(vectors, settings)
        _run(catalog.add_chunks(rows))
        _run(catalog.update_file_status(doc_id, "ready", chunks_count=len(rows), warnings=warnings))
        return IngestionResult(doc_id, name, file_type, len(rows), False, warnings)
    except AppError:
        _rollback(catalog, doc_id, created_vectors, settings, "Falha esperada durante a ingestão.")
        raise
    except Exception as error:
        _rollback(catalog, doc_id, created_vectors, settings, error)
        raise IngestionError("Falha inesperada durante a ingestão.") from error


def delete_document(doc_id: str, settings: Settings | None = None) -> None:
    """Delete vectors first, then storage and local catalog state."""
    settings = settings or get_settings()
    catalog = Catalog(settings.database_path)
    _run(catalog.initialize())
    record = _run(catalog.get_file(doc_id))
    if not record:
        raise FileNotFoundInCatalogError("Arquivo não encontrado")
    _run(catalog.update_file_status(doc_id, "deleting", chunks_count=record["chunks_count"], warnings=_warnings(record)))
    vector_ids = _run(catalog.get_vector_ids(doc_id))
    delete_vectors(vector_ids, settings)
    remove_storage(record["storage_key"], settings)
    _run(catalog.delete_file(doc_id))


def clear_index(settings: Settings | None = None) -> None:
    """Clear the configured Pinecone namespace, catalog and local storage."""
    settings = settings or get_settings()
    catalog = Catalog(settings.database_path)
    _run(catalog.initialize())
    delete_all_vectors(settings)
    _run(catalog.clear_catalog())
    for child in settings.uploads_dir.iterdir():
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    settings.derived_dir.mkdir(parents=True, exist_ok=True)


def _permanent_path(path: Path, settings: Settings) -> Path:
    """Ensure an input path is copied into permanent storage."""
    root = settings.uploads_dir.resolve()
    try:
        path.resolve().relative_to(root)
        return path.resolve()
    except ValueError:
        return stage_existing_file(path, settings).path


def _build_vectors(items: list[MediaItem], record: dict[str, Any], doc_id: str, settings: Settings) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Embed items and build flat Pinecone metadata plus catalog rows."""
    vectors: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    warnings = sorted({warning for item in items for warning in item.warnings})
    for index, item in enumerate(items):
        chunk_id = f"{doc_id}-chunk-{index}"
        vector_id = f"{doc_id}:{index}"
        vector = embed_text(prepare_document_text(item.text, record["original_name"]), settings) if item.content_modality == "text" else embed_media(item.media_path.read_bytes(), item.mime_type, settings)  # type: ignore[union-attr]
        media_key = storage_key_for_path(item.media_path, settings) if item.media_path else ""
        metadata = {"doc_id": doc_id, "chunk_id": chunk_id, "chunk_index": index, "original_name": record["original_name"], "stored_name": record["stored_name"], "storage_key": record["storage_key"], "file_type": item.file_type, "mime_type": item.mime_type or record["mime_type"], "content_modality": item.content_modality, "chunk_text": item.text, "text_preview": item.text[: settings.text_preview_size], "page_number": item.page_number, "media_key": media_key, "duration_seconds": item.duration_seconds, "ingested_at": record.get("created_at", "")}
        vectors.append({"id": vector_id, "values": vector, "metadata": metadata})
        rows.append({"chunk_id": chunk_id, "doc_id": doc_id, "vector_id": vector_id, "chunk_index": index, "page_number": item.page_number, "content_modality": item.content_modality, "media_key": media_key})
    return vectors, rows, warnings


def _rollback(catalog: Catalog, doc_id: str, vector_ids: list[str], settings: Settings, error: Exception | str) -> None:
    """Best-effort cleanup while keeping the original upload for diagnosis."""
    try:
        delete_vectors(vector_ids, settings)
    except Exception:
        logger.warning("ingestion_rollback_vectors_failed", extra={"doc_id": doc_id})
    try:
        _run(catalog.delete_chunks(doc_id))
        message = error if isinstance(error, str) else "Falha inesperada durante a ingestão."
        _run(catalog.update_file_status(doc_id, "failed", warnings=[], error_message=str(message)[:500]))
    except Exception:
        logger.warning("ingestion_rollback_catalog_failed", extra={"doc_id": doc_id})


def _warnings(record: dict[str, Any]) -> list[str]:
    """Decode catalog warnings safely."""
    try:
        return json.loads(record.get("warnings_json", "[]"))
    except json.JSONDecodeError:
        return []


def _run(coroutine):
    """Run one catalog coroutine from a synchronous service boundary."""
    return asyncio.run(coroutine)
