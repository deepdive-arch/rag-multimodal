"""Retryable coordination for Postgres, Pinecone, R2 and temporary files."""

from __future__ import annotations

import asyncio
import hmac
import logging
import shutil
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

from core.config import Settings, get_settings
from core.exceptions import DeletionError, FileNotFoundInCatalogError, ObjectStorageError
from db.catalog import Catalog
from services.pinecone_service import (
    confirm_namespace_empty,
    confirm_vectors_deleted,
    delete_all_vectors,
    delete_vectors,
)
from services.storage import document_object_prefix, get_object_storage, is_managed_object_key, object_storage_base_prefix, sanitize_filename


logger = logging.getLogger("rag_multimodal.deletion")


@dataclass(frozen=True)
class DeletionOutcome:
    """Safe result for one idempotent deletion attempt."""

    doc_id: str
    status: str
    stage: str | None = None
    claimed: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Serialize the public outcome."""
        return asdict(self)


async def delete_document_async(doc_id: str, settings: Settings, visitor_id: str | None = None) -> DeletionOutcome:
    """Delete one document in external-first order while retaining retry state."""
    catalog = Catalog.ephemeral(settings)
    try:
        plan = await catalog.begin_deletion(doc_id, settings.deletion_lease_seconds, visitor_id=visitor_id) if visitor_id else await catalog.begin_deletion(doc_id, settings.deletion_lease_seconds)
        outcome = _plan_outcome(doc_id, plan)
        if outcome is not None:
            return outcome
        stage = str(plan.get("stage") or "pinecone")
        try:
            if stage == "pinecone":
                await _delete_pinecone(plan, settings)
                stage = "r2"
                await catalog.mark_deletion_stage(doc_id, stage, lease_seconds=settings.deletion_lease_seconds)
            if stage == "r2":
                await _delete_r2(doc_id, plan, settings)
                stage = "postgres"
                await catalog.mark_deletion_stage(doc_id, stage, lease_seconds=settings.deletion_lease_seconds)
            await catalog.complete_deletion(doc_id)
            return DeletionOutcome(doc_id, "deleted", "completed", True)
        except Exception as error:
            await _remember_failure(catalog, doc_id, stage, settings, error)
            raise DeletionError(_safe_failure(stage)) from error
    finally:
        await catalog.close()
        _remove_document_temp(doc_id, settings)


def delete_document(doc_id: str, settings: Settings | None = None, visitor_id: str | None = None) -> DeletionOutcome:
    """Run one deletion from synchronous API workers and CLIs."""
    return asyncio.run(delete_document_async(doc_id, settings or get_settings(), visitor_id))


async def cleanup_pending_documents(settings: Settings, limit: int | None = None) -> list[dict[str, Any]]:
    """Process a small bounded batch of deletion and retention candidates."""
    batch_size = limit or settings.cleanup_batch_size
    catalog = Catalog.ephemeral(settings)
    outcomes: list[dict[str, Any]] = []
    try:
        candidates = await catalog.list_deleting_documents(batch_size)
        candidates += await catalog.list_expired_documents(max(0, batch_size - len(candidates)))
        for record in _unique_records(candidates)[:batch_size]:
            outcomes.append(await _cleanup_one(record["doc_id"], settings))
        return outcomes
    finally:
        await catalog.close()


async def cleanup_expired_documents(
    settings: Settings, *, limit: int, older_than: timedelta, dry_run: bool = False
) -> list[dict[str, Any]]:
    """List or delete bounded expired documents without a full-request scan."""
    catalog = Catalog.ephemeral(settings)
    try:
        records = await catalog.list_expired_documents(limit, older_than)
        if dry_run:
            return [_dry_run_record(record) for record in records]
        return [await _cleanup_one(record["doc_id"], settings) for record in records]
    finally:
        await catalog.close()


async def clear_namespace_async(settings: Settings, confirmation: str, admin_token: str | None) -> dict[str, Any]:
    """Clear only the configured environment namespace after explicit admin confirmation."""
    _require_clear_confirmation(settings, confirmation, admin_token)
    catalog = Catalog.ephemeral(settings)
    details = _clear_details(settings)
    try:
        await catalog.record_audit_event("clear_all_started", details)
        await asyncio.to_thread(delete_all_vectors, settings)
        await asyncio.to_thread(confirm_namespace_empty, settings)
        storage = get_object_storage(settings)
        objects = await storage.list_objects_by_prefix(object_storage_base_prefix(settings))
        keys = [item.key for item in objects]
        await _delete_and_confirm_r2(storage, keys)
        await catalog.clear_catalog()
        _remove_all_temp(settings)
        await catalog.record_audit_event("clear_all_completed", details | {"objects": len(keys)})
        return {"status": "cleared", "namespace": settings.pinecone_namespace, "objects": len(keys)}
    except Exception as error:
        await _record_audit_failure(catalog, details, error)
        raise DeletionError("A limpeza ampla ficou pendente; corrija a falha e tente novamente.") from error
    finally:
        await catalog.close()


def clear_namespace(
    settings: Settings | None = None, *, confirmation: str | None = None, admin_token: str | None = None
) -> dict[str, Any]:
    """Run the guarded namespace cleanup synchronously."""
    current = settings or get_settings()
    return asyncio.run(clear_namespace_async(current, confirmation or "", admin_token))


async def _delete_pinecone(plan: dict[str, Any], settings: Settings) -> None:
    """Delete and confirm only the document IDs in the configured namespace."""
    ids = [str(value) for value in plan.get("chunk_ids", [])]
    visitor_id = plan.get("visitor_id")
    await asyncio.to_thread(delete_vectors, ids, settings, visitor_id) if visitor_id else await asyncio.to_thread(delete_vectors, ids, settings)
    await asyncio.to_thread(confirm_vectors_deleted, ids, settings, visitor_id) if visitor_id else await asyncio.to_thread(confirm_vectors_deleted, ids, settings)


async def _delete_r2(doc_id: str, plan: dict[str, Any], settings: Settings) -> None:
    """Delete registered and discoverable objects under one document prefix."""
    storage = get_object_storage(settings)
    visitor_id = plan.get("visitor_id")
    listed = await storage.list_objects_by_prefix(document_object_prefix(doc_id, settings, visitor_id))
    registered = {str(value) for value in plan.get("object_keys", [])}
    if any(not is_managed_object_key(key, settings, visitor_id, doc_id) for key in registered):
        raise ObjectStorageError("Catálogo contém chave R2 fora do escopo do documento")
    keys = sorted(registered | {item.key for item in listed})
    await _delete_and_confirm_r2(storage, keys)


async def _delete_and_confirm_r2(storage: Any, keys: list[str]) -> None:
    """Delete objects idempotently and verify every key is absent."""
    if keys:
        await storage.delete_objects(keys)
    for key in keys:
        if await storage.head_object(key) is not None:
            raise ObjectStorageError("R2 ainda contém objetos do escopo excluído")


async def _remember_failure(catalog: Catalog, doc_id: str, stage: str, settings: Settings, error: Exception) -> None:
    """Persist a safe retry marker without provider details."""
    try:
        await catalog.mark_deletion_stage(
            doc_id, stage, error=_safe_failure(stage), lease_seconds=settings.deletion_lease_seconds
        )
    except Exception as error:
        logger.error("deletion_failure_state_persist_failed", extra={"doc_id": doc_id, "stage": stage, "error_type": type(error).__name__})


async def _cleanup_one(doc_id: str, settings: Settings) -> dict[str, Any]:
    """Convert one cleanup attempt into a JSON-safe result."""
    try:
        return delete_result_dict(await delete_document_async(doc_id, settings))
    except DeletionError:
        record = await _read_deletion_state(doc_id, settings)
        return record | {"status": "pending"}
    except FileNotFoundInCatalogError:
        return {"doc_id": doc_id, "status": "missing"}


async def _read_deletion_state(doc_id: str, settings: Settings) -> dict[str, Any]:
    """Read a safe state after an expected retryable failure."""
    catalog = Catalog.ephemeral(settings)
    try:
        record = await catalog.get_file(doc_id)
        return {"doc_id": doc_id, "stage": record.get("deletion_stage") if record else None}
    finally:
        await catalog.close()


def delete_result_dict(outcome: DeletionOutcome) -> dict[str, Any]:
    """Return a plain result for CLI output."""
    return outcome.as_dict()


def _plan_outcome(doc_id: str, plan: dict[str, Any]) -> DeletionOutcome | None:
    """Map non-claimed plans to idempotent public results."""
    if plan["status"] == "missing":
        raise FileNotFoundInCatalogError("Arquivo não encontrado")
    if plan["status"] == "deleted":
        return DeletionOutcome(doc_id, "deleted", "completed", False)
    if not plan.get("claimed"):
        return DeletionOutcome(doc_id, "deleting", plan.get("stage"), False)
    return None


def _unique_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one cleanup candidate per document."""
    return list({record["doc_id"]: record for record in records}.values())


def _dry_run_record(record: dict[str, Any]) -> dict[str, Any]:
    """Build a non-mutating cleanup preview."""
    return {"doc_id": record["doc_id"], "status": record["status"], "expires_at": record.get("expires_at")}


def _safe_failure(stage: str) -> str:
    """Return a stable client-safe retry message."""
    return f"Falha recuperável na exclusão ({stage}); tente novamente."


def _clear_details(settings: Settings) -> dict[str, str]:
    """Describe the narrow destructive scope without secrets."""
    return {"namespace": settings.pinecone_namespace, "r2_prefix": object_storage_base_prefix(settings)}


def _require_clear_confirmation(settings: Settings, confirmation: str, admin_token: str | None) -> None:
    """Require both the configured admin secret and exact destructive confirmation."""
    if not settings.admin_token or not admin_token or not hmac.compare_digest(admin_token, settings.admin_token):
        raise DeletionError("ADMIN_TOKEN é obrigatório para limpar a base")
    if confirmation != "DELETE_ALL":
        raise DeletionError("Confirmação DELETE_ALL é obrigatória")


async def _record_audit_failure(catalog: Catalog, details: dict[str, str], error: Exception) -> None:
    """Best-effort audit of a failed broad cleanup."""
    try:
        await catalog.record_audit_event("clear_all_failed", details | {"error_type": type(error).__name__})
    except Exception as error:
        logger.error("clear_all_audit_failed", extra={"error_type": type(error).__name__})


def _remove_document_temp(doc_id: str, settings: Settings) -> None:
    """Remove only the isolated temporary workspace for one document."""
    shutil.rmtree(settings.temp_processing_dir / sanitize_filename(doc_id), ignore_errors=True)


def _remove_all_temp(settings: Settings) -> None:
    """Remove temporary processing children while preserving the marker file."""
    for child in settings.temp_processing_dir.iterdir():
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
