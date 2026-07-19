"""Read-only persistence reconciliation with explicit opt-in corrections."""

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import get_settings  # noqa: E402
from db.catalog import Catalog  # noqa: E402
from services.deletion import delete_document_async  # noqa: E402
from services.pinecone_service import get_index  # noqa: E402
from services.storage import get_object_storage, object_storage_base_prefix  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    """Build the reconciliation argument parser."""
    parser = argparse.ArgumentParser(description="Reconcile Postgres, Pinecone and R2 persistence")
    parser.add_argument("--apply", action="store_true", help="apply only the documented safe corrections")
    return parser


async def reconcile(*, apply: bool) -> dict[str, Any]:
    """Inspect scoped persistence and optionally repair catalog/orphan state."""
    settings = get_settings()
    catalog = Catalog.ephemeral(settings)
    try:
        report = await _build_report(catalog, settings)
        report["dry_run"] = not apply
        if apply:
            report["applied"] = await _apply_corrections(report, catalog, settings)
        return report
    finally:
        await catalog.close()


async def _build_report(catalog: Catalog, settings) -> dict[str, Any]:
    """Collect all required consistency signals without mutating stores."""
    documents = await catalog.list_all_documents()
    document_ids = {row["doc_id"] for row in documents if row["status"] != "deleted"}
    without_objects = await catalog.list_documents_without_objects()
    ready_without_objects = [row["doc_id"] for row in documents if row["status"] == "ready" and row["doc_id"] in without_objects]
    ready_without_chunks = await catalog.list_ready_without_chunks()
    chunks_by_document = await catalog.list_chunk_ids_by_document()
    missing_vectors = await _missing_vectors(chunks_by_document, settings)
    pending_deletions = [row["doc_id"] for row in await catalog.list_deleting_documents(100)]
    stale_processing = await catalog.list_stuck_documents("processing", timedelta(seconds=settings.deletion_lease_seconds), 100)
    stale_deleting = await catalog.list_stuck_documents("deleting", timedelta(seconds=settings.deletion_lease_seconds), 100)
    r2_orphans = await _r2_orphans(settings, document_ids)
    return {
        "documents_without_objects": without_objects,
        "ready_without_objects": ready_without_objects,
        "r2_objects_without_document": r2_orphans,
        "ready_without_chunks": ready_without_chunks,
        "chunks_missing_in_pinecone": missing_vectors,
        "vectors_for_removed_documents": await _removed_vector_audit(documents, settings),
        "processing_stuck": stale_processing,
        "deleting_pending": pending_deletions,
        "deleting_stuck": stale_deleting,
        "dry_run": False,
    }


async def _missing_vectors(chunks_by_document: dict[str, list[str]], settings) -> dict[str, list[str]]:
    """Fetch Postgres IDs in Pinecone and return only absent IDs."""
    missing: dict[str, list[str]] = {}
    for doc_id, chunk_ids in chunks_by_document.items():
        absent = await _missing_ids(chunk_ids, settings)
        if absent:
            missing[doc_id] = absent
    return missing


async def _missing_ids(chunk_ids: list[str], settings) -> list[str]:
    """Read vector presence without deleting anything."""
    if not chunk_ids:
        return []
    response = await asyncio.to_thread(get_index(settings).fetch, ids=chunk_ids, namespace=settings.pinecone_namespace)
    vectors = getattr(response, "vectors", None) or (response.get("vectors", {}) if isinstance(response, dict) else {})
    return [chunk_id for chunk_id in chunk_ids if chunk_id not in vectors]


async def _r2_orphans(settings, document_ids: set[str]) -> list[str]:
    """List only objects below the configured environment/namespace prefix."""
    objects = await get_object_storage(settings).list_objects_by_prefix(object_storage_base_prefix(settings))
    return [item.key for item in objects if _key_document_id(item.key, settings) not in document_ids]


async def _removed_vector_audit(documents: list[dict[str, Any]], settings) -> dict[str, Any] | list[str]:
    """Report removed-vector inventory when the Pinecone SDK exposes list IDs."""
    removed = {row["doc_id"] for row in documents if row["status"] == "deleted"}
    if not removed:
        return []
    try:
        response = await asyncio.to_thread(get_index(settings).list, namespace=settings.pinecone_namespace, limit=10_000)
        ids = _listed_vector_ids(response)
        return [value for value in ids if any(value == doc_id or value.startswith(f"{doc_id}-") for doc_id in removed)]
    except Exception as error:
        return {"status": "not_checked", "reason": type(error).__name__}


def _listed_vector_ids(response: Any) -> list[str]:
    """Normalize common Pinecone list response shapes without logging payloads."""
    if isinstance(response, dict):
        values = response.get("vectors") or response.get("ids") or []
    else:
        values = getattr(response, "vectors", None) or getattr(response, "ids", None) or []
    return [str(item.get("id") if isinstance(item, dict) else getattr(item, "id", item)) for item in values]


def _key_document_id(key: str, settings) -> str:
    """Extract the controlled document segment from a managed R2 key."""
    prefix = f"{object_storage_base_prefix(settings)}/"
    remainder = key.removeprefix(prefix)
    return remainder.split("/", 1)[0]


async def _apply_corrections(report: dict[str, Any], catalog: Catalog, settings) -> dict[str, int]:
    """Apply only safe status repairs, orphan deletion and pending retries."""
    marked_failed = 0
    missing_vector_documents = list(report["chunks_missing_in_pinecone"])
    repair_candidates = report["documents_without_objects"] + report["ready_without_chunks"] + missing_vector_documents
    for doc_id in repair_candidates:
        record = await catalog.get_file(doc_id)
        if record and record["status"] == "ready":
            await catalog.update_status(doc_id, "failed", error_message="Reconciliação detectou persistência incompleta.")
            marked_failed += 1
    deleted_orphans = await _delete_orphans(report["r2_objects_without_document"], settings)
    retried = 0
    for doc_id in report["deleting_pending"]:
        await delete_document_async(doc_id, settings)
        retried += 1
    return {"marked_failed": marked_failed, "deleted_r2_orphans": deleted_orphans, "retried_deletions": retried}


async def _delete_orphans(keys: list[str], settings) -> int:
    """Delete only explicitly scoped R2 orphans when --apply was given."""
    if not keys:
        return 0
    storage = get_object_storage(settings)
    await storage.delete_objects(keys)
    if any(await storage.head_object(key) is not None for key in keys):
        raise RuntimeError("R2 orphan deletion could not be confirmed")
    return len(keys)


def main() -> None:
    """Parse arguments and print the reconciliation report."""
    args = _parser().parse_args()
    print(json.dumps(asyncio.run(reconcile(apply=args.apply)), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
