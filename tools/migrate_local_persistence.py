"""Opt-in, read-only-source migration from the former SQLite/local store.

The command is deliberately outside the runtime image.  It reads the legacy
SQLite file through SQLite's read-only URI, never deletes local files, and
defaults to a dry run.  ``--apply`` is required before R2 or Postgres writes;
``--reindex-missing`` is the only switch that permits new embeddings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import Settings, get_settings  # noqa: E402
from db.catalog import Catalog  # noqa: E402
from services.ingestion import process_uploaded_document  # noqa: E402
from services.pinecone_service import delete_vectors, get_index  # noqa: E402
from services.storage import (  # noqa: E402
    derived_object_key,
    get_object_storage,
    mime_for,
    original_object_key,
    original_object_metadata,
    sanitize_filename,
    sha256_for_path,
)


DEFAULT_SQLITE_PATH = Path(".tmp/rag.db")
DEFAULT_UPLOADS_DIR = Path(".tmp/uploads")
_STATUSES = {"pending_upload", "uploaded", "processing", "indexing", "ready", "failed", "deleting", "deleted"}
_STATUS_ALIASES = {"complete": "ready", "completed": "ready"}


@dataclass(frozen=True)
class LegacyDocument:
    doc_id: str
    original_name: str
    stored_name: str
    storage_key: str
    file_type: str
    mime_type: str
    size_bytes: int
    status: str
    chunks_count: int
    warnings: list[str]
    error_message: str | None
    created_at: str | None
    updated_at: str | None


@dataclass(frozen=True)
class LegacyChunk:
    chunk_id: str
    doc_id: str
    vector_id: str
    ordinal: int
    page_number: int
    content_modality: str
    media_key: str


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, execute the migration, and emit only safe JSON."""
    args = _parser().parse_args(argv)
    if args.reindex_missing and not args.apply:
        _parser_error("--reindex-missing requires --apply")
    report = asyncio.run(_run(args))
    _write_report(Path(args.report_path), report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["counts"]["errors"] == 0 else 1


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    """Read legacy state and optionally copy it to the managed stores."""
    sqlite_path = Path(args.sqlite_path)
    uploads_root = Path(args.uploads_dir)
    base = {"dry_run": not args.apply, "apply": bool(args.apply), "errors": [], "documents": []}
    try:
        documents, chunks = _read_legacy(sqlite_path, args.document_id, args.limit)
    except Exception as error:
        base["errors"].append(_safe_error(error))
        return _finalize_report(base, sqlite_path, uploads_root)
    if not args.apply:
        return _dry_run_report(base, documents, chunks, sqlite_path, uploads_root, args.reindex_missing)
    try:
        settings = get_settings()
        catalog = Catalog.ephemeral(settings)
        storage = get_object_storage(settings)
        for document in documents:
            try:
                await _migrate_document(catalog, storage, settings, document, chunks.get(document.doc_id, []), uploads_root, args, base)
            except Exception as error:
                base["documents"].append(_failed_document(document, error))
    except Exception as error:
        base["errors"].append(_safe_error(error))
    finally:
        if "catalog" in locals():
            try:
                await catalog.close()
            except Exception as error:
                base["errors"].append(_safe_error(error))
    return _finalize_report(base, sqlite_path, uploads_root)


def _read_legacy(path: Path, document_id: str | None, limit: int | None) -> tuple[list[LegacyDocument], dict[str, list[LegacyChunk]]]:
    """Read known legacy tables through a SQLite read-only connection."""
    resolved = path.resolve()
    connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        documents = _read_documents(connection, document_id, limit)
        chunks = _read_chunks(connection, {item.doc_id for item in documents})
        return documents, chunks
    finally:
        connection.close()


def _read_documents(connection: sqlite3.Connection, document_id: str | None, limit: int | None) -> list[LegacyDocument]:
    """Load legacy document rows without modifying the source database."""
    query = "SELECT * FROM ingested_files"
    params: list[Any] = []
    if document_id:
        query += " WHERE doc_id = ?"
        params.append(document_id)
    query += " ORDER BY created_at, doc_id"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    return [_document_from_row(row) for row in connection.execute(query, params)]


def _read_chunks(connection: sqlite3.Connection, document_ids: set[str]) -> dict[str, list[LegacyChunk]]:
    """Load only chunks belonging to the selected legacy documents."""
    if not document_ids:
        return {}
    placeholders = ",".join("?" for _ in document_ids)
    query = f"SELECT * FROM chunks WHERE doc_id IN ({placeholders}) ORDER BY doc_id, chunk_index, chunk_id"
    grouped: dict[str, list[LegacyChunk]] = {}
    for row in connection.execute(query, tuple(document_ids)):
        item = LegacyChunk(
            chunk_id=str(row["chunk_id"]),
            doc_id=str(row["doc_id"]),
            vector_id=str(row["vector_id"] or ""),
            ordinal=int(row["chunk_index"] or 0),
            page_number=int(row["page_number"] or 0),
            content_modality=str(row["content_modality"] or "text"),
            media_key=str(row["media_key"] or ""),
        )
        grouped.setdefault(item.doc_id, []).append(item)
    return grouped


def _document_from_row(row: sqlite3.Row) -> LegacyDocument:
    """Normalize one legacy row without retaining arbitrary fields."""
    return LegacyDocument(
        doc_id=str(row["doc_id"]),
        original_name=str(row["original_name"] or row["stored_name"] or "upload"),
        stored_name=str(row["stored_name"] or row["original_name"] or "upload"),
        storage_key=str(row["storage_key"] or ""),
        file_type=str(row["file_type"] or "text"),
        mime_type=str(row["mime_type"] or "application/octet-stream"),
        size_bytes=int(row["size_bytes"] or 0),
        status=_status(str(row["status"] or "failed")),
        chunks_count=int(row["chunks_count"] or 0),
        warnings=_warnings(row["warnings_json"]),
        error_message=str(row["error_message"]) if row["error_message"] else None,
        created_at=str(row["created_at"]) if row["created_at"] else None,
        updated_at=str(row["updated_at"]) if row["updated_at"] else None,
    )


async def _migrate_document(catalog: Catalog, storage: Any, settings: Settings, document: LegacyDocument, chunks: list[LegacyChunk], uploads_root: Path, args: argparse.Namespace, report: dict[str, Any]) -> None:
    """Migrate one document, preserving identity and avoiding duplicate writes."""
    item: dict[str, Any] = {"doc_id": document.doc_id, "status": document.status, "action": "skipped", "warnings": [], "errors": []}
    try:
        doc_uuid = str(UUID(document.doc_id))
    except ValueError:
        item["errors"].append("invalid_doc_id")
        report["documents"].append(item)
        return
    original = _locate_file(uploads_root, document.storage_key, document.stored_name, document.original_name)
    if original is None:
        item["errors"].append("original_not_found")
        report["documents"].append(item)
        return
    sha256 = sha256_for_path(original)
    derived = _derived_files(uploads_root, chunks, original)
    item.update({"sha256": sha256, "objects": 1 + len(derived), "chunks": len(chunks)})
    if any(not chunk.vector_id for chunk in chunks):
        item["warnings"].append("missing_vector_id")
    if any(chunk.vector_id and chunk.vector_id != chunk.chunk_id for chunk in chunks):
        item["warnings"].append("vector_id_used_as_chunk_id")
    existing = await catalog.get_file(doc_uuid)
    by_hash = await catalog.find_by_sha256(sha256)
    if existing and existing["sha256"] != sha256:
        item["errors"].append("doc_id_conflict")
        report["documents"].append(item)
        return
    if by_hash and by_hash["doc_id"] != doc_uuid:
        item["errors"].append("sha256_conflict")
        report["documents"].append(item)
        return
    await _upload_objects(storage, settings, doc_uuid, document, original, derived)
    if existing:
        item["action"] = "already_migrated"
        item["warnings"].append("document_exists")
    else:
        await catalog.create_document(_document_record(document, doc_uuid, original, sha256, settings))
        item["action"] = "created"
    object_ids = await _register_missing_objects(catalog, settings, doc_uuid, document, original, derived)
    await _register_missing_chunks(catalog, settings, doc_uuid, chunks, object_ids)
    if args.reindex_missing and await _requires_reindex(catalog, settings, doc_uuid, chunks):
        await _reindex_document(catalog, settings, doc_uuid, item)
    if not chunks and document.status == "ready":
        item["warnings"].append("ready_without_chunks")
    report["documents"].append(item)


async def _upload_objects(storage: Any, settings: Settings, doc_id: str, document: LegacyDocument, original: Path, derived: list[Path]) -> None:
    """Upload the original and all referenced derived files to private R2."""
    original_key = original_object_key(doc_id, document.original_name, settings)
    await _put_if_needed(storage, original_key, original, document.mime_type, original_object_metadata(doc_id, sha256_for_path(original), document.original_name, document.mime_type, settings))
    for path in derived:
        key = derived_object_key(doc_id, path.name, settings)
        await _put_if_needed(storage, key, path, mime_for(path), {"doc-id": doc_id, "sha256": sha256_for_path(path), "upload-version": settings.r2_upload_version})


async def _put_if_needed(storage: Any, key: str, path: Path, content_type: str, metadata: dict[str, str]) -> None:
    """Avoid rewriting an intact R2 object on an idempotent rerun."""
    remote = await storage.head_object(key)
    remote_metadata = remote.metadata or {} if remote else {}
    matches = remote and remote.size_bytes == path.stat().st_size and remote.content_type == content_type and remote_metadata.get("sha256") == metadata.get("sha256")
    if not matches:
        await storage.put_file(key, path, content_type=content_type, metadata=metadata)


async def _requires_reindex(catalog: Catalog, settings: Settings, doc_id: str, chunks: list[LegacyChunk]) -> bool:
    """Check legacy IDs and the configured namespace only after explicit opt-in."""
    if not chunks or any(not chunk.vector_id for chunk in chunks):
        return True
    vector_ids = await catalog.get_vector_ids(doc_id)
    response = await asyncio.to_thread(get_index(settings).fetch, ids=vector_ids, namespace=settings.pinecone_namespace)
    vectors = getattr(response, "vectors", None) or (response.get("vectors", {}) if isinstance(response, dict) else {})
    return any(vector_id not in vectors for vector_id in vector_ids)


async def _register_missing_objects(catalog: Catalog, settings: Settings, doc_id: str, document: LegacyDocument, original: Path, derived: list[Path]) -> dict[str, str]:
    """Register source and derived references, relying on unique object keys for idempotence."""
    specs = [{"doc_id": doc_id, "object_kind": "original", "object_key": original_object_key(doc_id, document.original_name, settings), "mime_type": document.mime_type, "size_bytes": original.stat().st_size, "sha256": sha256_for_path(original)}]
    specs.extend({"doc_id": doc_id, "object_kind": "derived", "object_key": derived_object_key(doc_id, path.name, settings), "mime_type": mime_for(path), "size_bytes": path.stat().st_size, "sha256": sha256_for_path(path)} for path in derived)
    return await catalog.register_objects(specs)


async def _register_missing_chunks(catalog: Catalog, settings: Settings, doc_id: str, chunks: list[LegacyChunk], object_ids: dict[str, str]) -> None:
    """Register only new chunk IDs; vector IDs are preserved as chunk IDs when present."""
    rows = [{"doc_id": doc_id, "chunk_id": chunk.vector_id or chunk.chunk_id, "ordinal": chunk.ordinal, "page_number": chunk.page_number, "content_modality": chunk.content_modality, "object_id": object_ids.get(derived_object_key(doc_id, Path(chunk.media_key).name, settings)) if chunk.media_key else None} for chunk in chunks]
    await catalog.register_chunks(rows)


async def _reindex_document(catalog: Catalog, settings: Settings, doc_id: str, item: dict[str, Any]) -> None:
    """Reprocess only when the operator explicitly requested missing vectors."""
    record = await catalog.get_file(doc_id)
    if not record or record["status"] not in {"ready", "failed", "processing"}:
        item["warnings"].append("reindex_skipped_for_status")
        return
    vector_ids = await catalog.get_vector_ids(doc_id)
    await asyncio.to_thread(delete_vectors, vector_ids, settings)
    await catalog.clear_processing_artifacts(doc_id)
    record = await catalog.get_file(doc_id)
    if record and record["status"] != "processing":
        await catalog.update_status(doc_id, "processing")
    result = await asyncio.to_thread(process_uploaded_document, doc_id, settings)
    if result is None:
        item["errors"].append("reindex_failed")
    else:
        item["warnings"].append("reindexed_missing_vectors")


def _document_record(document: LegacyDocument, doc_id: str, original: Path, sha256: str, settings: Settings) -> dict[str, Any]:
    """Build the new catalog record without copying legacy local paths."""
    return {"doc_id": doc_id, "original_name": document.original_name, "sanitized_name": sanitize_filename(document.stored_name), "object_key": original_object_key(doc_id, document.original_name, settings), "file_type": document.file_type, "mime_type": document.mime_type, "size_bytes": original.stat().st_size, "sha256": sha256, "status": document.status, "chunks_count": document.chunks_count, "warnings": document.warnings, "error_message": document.error_message, "created_at": document.created_at, "updated_at": document.updated_at}


def _derived_files(root: Path, chunks: list[LegacyChunk], original: Path) -> list[Path]:
    """Resolve referenced legacy media without allowing traversal outside uploads."""
    paths = {_locate_file(root, chunk.media_key) for chunk in chunks if chunk.media_key}
    return sorted(path for path in paths if path and path != original)


def _locate_file(root: Path, *names: str) -> Path | None:
    """Resolve only regular files below the legacy uploads directory."""
    root = root.resolve()
    for name in names:
        if not name or Path(name).is_absolute():
            continue
        candidate = (root / name.replace("uploads/", "", 1)).resolve()
        if root in candidate.parents and candidate.is_file():
            return candidate
        candidate = (root / Path(name).name).resolve()
        if root in candidate.parents and candidate.is_file():
            return candidate
    return None


def _dry_run_report(base: dict[str, Any], documents: list[LegacyDocument], chunks: dict[str, list[LegacyChunk]], sqlite_path: Path, uploads_root: Path, reindex_missing: bool) -> dict[str, Any]:
    """Describe intended operations without opening Postgres or R2."""
    for document in documents:
        item = {"doc_id": document.doc_id, "status": document.status, "action": "would_migrate", "chunks": len(chunks.get(document.doc_id, [])), "warnings": [], "errors": []}
        if not _locate_file(uploads_root, document.storage_key, document.stored_name, document.original_name):
            item["errors"].append("original_not_found")
        if reindex_missing:
            item["warnings"].append("reindex_requested")
        base["documents"].append(item)
    return _finalize_report(base, sqlite_path, uploads_root)


def _finalize_report(base: dict[str, Any], sqlite_path: Path, uploads_root: Path) -> dict[str, Any]:
    """Add bounded, non-secret summary metadata to the report."""
    base["sqlite_path"] = _safe_relative(sqlite_path)
    base["uploads_dir"] = _safe_relative(uploads_root)
    base["counts"] = {"documents_seen": len(base["documents"]), "created": sum(item["action"] == "created" for item in base["documents"]), "already_migrated": sum(item["action"] == "already_migrated" for item in base["documents"]), "errors": len(base["errors"]) + sum(bool(item["errors"]) for item in base["documents"])}
    return base


def _write_report(path: Path, report: dict[str, Any]) -> None:
    """Write the requested JSON report atomically without printing credentials."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _status(value: str) -> str:
    """Map legacy status names to the current enum."""
    mapped = _STATUS_ALIASES.get(value.lower(), value.lower())
    return mapped if mapped in _STATUSES else "failed"


def _warnings(value: Any) -> list[str]:
    """Decode only the legacy warning list."""
    try:
        decoded = json.loads(value or "[]")
        return [str(item) for item in decoded] if isinstance(decoded, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def _safe_error(error: Exception) -> str:
    """Return an exception class only, preventing credentials in reports."""
    return type(error).__name__


def _failed_document(document: LegacyDocument, error: Exception) -> dict[str, Any]:
    """Keep one failed record safe while allowing later documents to continue."""
    return {"doc_id": document.doc_id, "status": document.status, "action": "failed", "warnings": [], "errors": [_safe_error(error)]}


def _safe_relative(path: Path) -> str:
    """Avoid emitting machine-specific absolute paths in the JSON report."""
    try:
        return os.fspath(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return "<external-path>"


def _parser() -> argparse.ArgumentParser:
    """Create the migration CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="only inspect and report; this is the default")
    mode.add_argument("--apply", action="store_true", help="write R2 and Postgres state")
    parser.add_argument("--limit", type=_positive_int, default=None)
    parser.add_argument("--document-id", default=None)
    parser.add_argument("--reindex-missing", action="store_true", help="explicitly regenerate embeddings for chunks without vector IDs")
    parser.add_argument("--report-path", default=".tmp/migration-report.json")
    parser.add_argument("--sqlite-path", default=os.getenv("LEGACY_SQLITE_PATH", os.fspath(DEFAULT_SQLITE_PATH)))
    parser.add_argument("--uploads-dir", default=os.getenv("LEGACY_UPLOADS_DIR", os.fspath(DEFAULT_UPLOADS_DIR)))
    return parser


def _positive_int(value: str) -> int:
    """Parse a strictly positive bounded CLI integer."""
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _parser_error(message: str) -> None:
    """Raise a parser-style usage error without a second parser instance."""
    raise SystemExit(f"error: {message}")


if __name__ == "__main__":
    raise SystemExit(main())
