"""Async Postgres catalog repository with compatibility aliases for the MVP."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, or_, select, text, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import ColumnElement

from core.config import Settings
from core.exceptions import CapacityExceededError, CatalogError, FileNotFoundInCatalogError, QuotaExceededError
from core.visitor import parse_visitor_id
from db.database import Database
from db.interfaces import CatalogRepository
from db.models import AuditEvent, Chunk, Conversation, ConversationMessage, Document, DocumentObject, Feedback, IngestionEvent, UsageCounter
from db.runtime import get_database


_STATUSES = {"pending_upload", "uploaded", "processing", "indexing", "ready", "failed", "deleting", "deleted"}
_TRANSITIONS = {
    "pending_upload": {"uploaded", "failed", "deleting"},
    "uploaded": {"processing", "failed", "deleting"},
    "processing": {"indexing", "ready", "failed", "deleting"},
    "indexing": {"ready", "failed", "deleting"},
    "ready": {"processing", "deleting"},
    "failed": {"processing", "deleting"},
    "deleting": {"deleted"},
    "deleted": set(),
}
_DELETION_STAGES = {"pinecone", "r2", "postgres", "completed"}


def utc_now() -> str:
    """Return an ISO timestamp in UTC for external metadata."""
    return datetime.now(UTC).isoformat()


def _uuid(value: Any, *, fallback: UUID | None = None) -> UUID:
    """Parse a UUID or use an explicit fallback."""
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        if fallback is not None:
            return fallback
        raise ValueError("document identifiers must be UUIDs") from None


def _datetime(value: Any) -> datetime | None:
    """Normalize optional timestamps to aware UTC datetimes."""
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _warnings(value: Any) -> list[str]:
    """Normalize JSON warnings without exposing malformed values."""
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        decoded = json.loads(value or "[]")
        return decoded if isinstance(decoded, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def _record(document: Document, original: DocumentObject | None) -> dict[str, Any]:
    """Map ORM rows to the historical catalog shape."""
    warnings = _warnings(document.warnings)
    return {
        "doc_id": str(document.doc_id),
        "visitor_id": str(document.visitor_id) if document.visitor_id else None,
        "original_name": document.original_name,
        "sanitized_name": document.sanitized_name,
        "stored_name": document.sanitized_name,
        "storage_key": original.object_key if original else "",
        "object_key": original.object_key if original else "",
        "object_id": str(original.object_id) if original else None,
        "file_type": document.file_type,
        "mime_type": document.mime_type,
        "sha256": document.sha256,
        "size_bytes": document.size_bytes,
        "status": document.status,
        "chunks_count": document.chunks_count,
        "warnings": warnings,
        "warnings_json": json.dumps(warnings),
        "safe_error_message": document.safe_error_message,
        "error_message": document.safe_error_message,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
        "expires_at": document.expires_at.isoformat() if document.expires_at else None,
        "upload_expires_at": document.upload_expires_at.isoformat() if document.upload_expires_at else None,
        "deleted_at": document.deleted_at.isoformat() if document.deleted_at else None,
        "deletion_stage": document.deletion_stage,
        "deletion_error": document.deletion_error,
        "deletion_started_at": document.deletion_started_at.isoformat() if document.deletion_started_at else None,
        "deletion_lease_until": document.deletion_lease_until.isoformat() if document.deletion_lease_until else None,
    }


class Catalog(CatalogRepository):
    """Postgres persistence gateway for documents and operational records."""

    def __init__(self, database: Database | Settings | str | None = None) -> None:
        self._owns_database = isinstance(database, str)
        self._settings = database if isinstance(database, Settings) else None
        self._health_timeout = database.database_health_timeout_seconds if isinstance(database, Settings) else 2.0
        self.database = (
            database
            if isinstance(database, Database)
            else get_database(database)
            if isinstance(database, Settings) or database is None
            else Database(database)
        )

    @classmethod
    def ephemeral(cls, settings: Settings) -> "Catalog":
        """Create an isolated catalog for a synchronous worker event loop."""
        catalog = cls(Database(settings))
        catalog._settings = settings
        catalog._owns_database = True
        return catalog

    async def initialize(self) -> None:
        """Check connectivity without creating tables; Alembic owns schema creation."""
        await self.is_ready()

    async def close(self) -> None:
        """Dispose an engine owned by this catalog."""
        if self._owns_database:
            await self.database.close()

    async def is_ready(self, timeout_seconds: float | None = None) -> bool:
        """Check that Postgres answers a bounded trivial query."""
        await self.database.check(timeout_seconds or self._health_timeout)
        return True

    async def get_file(self, doc_id: str, visitor_id: str | None = None) -> dict[str, Any] | None:
        """Load a document by UUID, with SHA-256 compatibility fallback."""
        try:
            condition = Document.doc_id == _uuid(doc_id) if _is_uuid(doc_id) else Document.sha256 == doc_id
            condition = _with_document_owner(condition, visitor_id)
        except ValueError:
            return None
        return await self._fetch_one(condition)

    async def find_by_sha256(self, sha256: str, visitor_id: str | None = None) -> dict[str, Any] | None:
        """Find an active document by its unique content hash."""
        condition = and_(
            Document.sha256 == sha256,
            Document.status != "deleted",
        )
        return await self._fetch_one(_with_document_owner(condition, visitor_id))

    async def create_document(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Insert a document and its original object in one transaction."""
        values = _document_values(record, retention_days=self._retention_days())
        try:
            async with self.database.session_factory.begin() as session:
                document = Document(**values[0])
                session.add(document)
                await session.flush()
                session.add(DocumentObject(**values[1], document_id=document.doc_id))
                session.add(
                    IngestionEvent(
                        document_id=document.doc_id,
                        event_type="document_created",
                        safe_details={"status": document.status},
                    )
                )
            created = await self.get_file(str(document.doc_id))
            return {**created, "created": True} if created else {"doc_id": str(document.doc_id), "created": True}
        except IntegrityError:
            existing = await self.find_by_sha256(values[0]["sha256"], _record_visitor(values[0]))
            if existing:
                return {**existing, "created": False}
            raise CatalogError("Não foi possível registrar o documento") from None

    async def create_document_with_quota(
        self, record: Mapping[str, Any], client_hash: str, usage_date: date
    ) -> dict[str, Any]:
        """Create a document and reserve its public quota in one transaction."""
        settings = self._settings
        if settings is None:
            raise CatalogError("Configuração de quota pública indisponível")
        values = _document_values(record, retention_days=self._retention_days())
        created_id, created = await self._create_quota_document(values, client_hash, usage_date, settings)
        current = await self.get_file(str(created_id))
        return {**(current or {"doc_id": str(created_id)}), "created": created}

    async def _create_quota_document(self, values, client_hash: str, usage_date: date, settings: Settings) -> tuple[UUID, bool]:
        """Serialize public upload reservations with a short Postgres advisory lock."""
        async with self.database.session_factory.begin() as session:
            await _lock_public_quota(session)
            existing_condition = and_(
                Document.sha256 == values[0]["sha256"],
                Document.status != "deleted",
            )
            existing = await session.scalar(
                select(Document).where(
                    _with_document_owner(existing_condition, _record_visitor(values[0]))
                )
            )
            if existing:
                return existing.doc_id, False
            counter = await _locked_usage_counter(session, client_hash, usage_date)
            _check_daily_upload_quota(counter, settings)
            await _check_global_capacity(session, values[0]["size_bytes"], settings)
            document = Document(**values[0])
            session.add(document)
            await session.flush()
            session.add(DocumentObject(**values[1], document_id=document.doc_id))
            session.add(IngestionEvent(document_id=document.doc_id, event_type="document_created", safe_details={"status": document.status}))
            _increment_counter(counter, uploads=1, bytes_uploaded=values[0]["size_bytes"])
            return document.doc_id, True

    async def create_processing_file(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Compatibility method that inserts or resets a processing document."""
        existing = await self.find_by_sha256(str(record.get("sha256", record.get("doc_id", ""))))
        if existing:
            await self.reset_document(existing["doc_id"], record)
            return await self.get_file(existing["doc_id"]) or existing
        return await self.create_document(record)

    async def reset_document(self, doc_id: str, record: Mapping[str, Any]) -> None:
        """Reset a failed or forced document without changing its identity."""
        values = _document_values({**record, "doc_id": doc_id})
        async with self.database.session_factory.begin() as session:
            document = await session.get(Document, values[0]["doc_id"])
            if document is None:
                raise CatalogError("Documento não encontrado")
            previous = document.status
            for key, value in values[0].items():
                if key not in {"doc_id", "created_at"}:
                    setattr(document, key, value)
            document.status = "processing"
            document.chunks_count = 0
            document.warnings = []
            document.safe_error_message = None
            document.deleted_at = None
            document.deletion_stage = None
            document.deletion_error = None
            document.deletion_started_at = None
            document.deletion_lease_until = None
            document.expires_at = _retention_expiry(self._retention_days())
            document.upload_expires_at = None
            document.updated_at = datetime.now(UTC)
            await session.execute(delete(Chunk).where(Chunk.document_id == document.doc_id))
            await session.execute(
                delete(DocumentObject).where(
                    and_(DocumentObject.document_id == document.doc_id, DocumentObject.object_kind == "derived")
                )
            )
            if previous != "processing":
                session.add(
                    IngestionEvent(
                        document_id=document.doc_id,
                        event_type="status_transition",
                        safe_details={"from_status": previous, "to_status": "processing"},
                    )
                )

    async def update_file_status(
        self,
        doc_id: str,
        status: str,
        *,
        chunks_count: int = 0,
        warnings: list[str] | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update an operational status after validating the transition."""
        await self.update_status(
            doc_id, status, chunks_count=chunks_count, warnings=warnings, error_message=error_message
        )

    async def mark_uploaded(self, doc_id: str, visitor_id: str | None = None) -> bool:
        """Promote one pending upload and clear its temporary upload expiry."""
        now = datetime.now(UTC)
        async with self.database.session_factory.begin() as session:
            updated_doc_id = await session.scalar(
                update(Document)
                .where(
                    Document.doc_id == _uuid(doc_id),
                    Document.status == "pending_upload",
                    _document_owner_clause(visitor_id),
                )
                .values(
                    status="uploaded",
                    upload_expires_at=None,
                    updated_at=now,
                )
                .returning(Document.doc_id)
            )
            if updated_doc_id is not None:
                session.add(
                    IngestionEvent(
                        document_id=updated_doc_id,
                        event_type="status_transition",
                        safe_details={
                            "from_status": "pending_upload",
                            "to_status": "uploaded",
                        },
                    )
                )
        return updated_doc_id is not None

    async def renew_upload(self, doc_id: str, expires_at: Any, visitor_id: str | None = None) -> None:
        """Refresh the server-side expiry for a pending upload."""
        async with self.database.session_factory.begin() as session:
            await session.execute(
                update(Document)
                .where(Document.doc_id == _uuid(doc_id), Document.status == "pending_upload", _document_owner_clause(visitor_id))
                .values(upload_expires_at=_datetime(expires_at), updated_at=datetime.now(UTC))
            )

    async def claim_processing(self, doc_id: str, visitor_id: str | None = None) -> bool:
        """Atomically claim an uploaded document for one worker."""
        return await self._conditional_status(doc_id, "uploaded", "processing", visitor_id)

    async def claim_retry(self, doc_id: str) -> bool:
        """Atomically claim an explicitly requested retry."""
        return await self._conditional_status(doc_id, "failed", "processing")

    async def update_status(
        self,
        doc_id: str,
        status: str,
        *,
        chunks_count: int = 0,
        warnings: list[str] | None = None,
        error_message: str | None = None,
    ) -> None:
        """Persist a status transition in one transaction."""
        if status not in _STATUSES:
            raise ValueError("status inválido")
        async with self.database.session_factory.begin() as session:
            document = await session.get(Document, _uuid(doc_id))
            if document is None:
                raise CatalogError("Documento não encontrado")
            if document.status != status and status not in _TRANSITIONS[document.status]:
                raise ValueError("transição de status inválida")
            previous = document.status
            document.status, document.chunks_count, document.warnings = status, chunks_count, _warnings(warnings)
            document.safe_error_message, document.updated_at = error_message, datetime.now(UTC)
            if status == "deleting":
                document.deleted_at = None
            if previous != status:
                session.add(
                    IngestionEvent(
                        document_id=document.doc_id,
                        event_type="status_transition",
                        safe_details={"from_status": previous, "to_status": status},
                    )
                )

    async def add_chunks(self, chunks: list[dict[str, Any]]) -> None:
        """Compatibility method for registering vector traceability rows."""
        await self.register_chunks(chunks)

    async def register_chunks(self, chunks: list[dict[str, Any]]) -> None:
        """Insert chunk references and derived-object references in one transaction."""
        if not chunks:
            return
        async with self.database.session_factory.begin() as session:
            derived: dict[str, DocumentObject] = {}
            for item in chunks:
                if await session.get(Chunk, str(item["chunk_id"])):
                    continue
                object_id = (
                    _uuid(item["object_id"])
                    if item.get("object_id")
                    else await self._derived_object(session, item, derived)
                )
                session.add(
                    Chunk(
                        chunk_id=str(item["chunk_id"]),
                        document_id=_uuid(item["doc_id"]),
                        ordinal=int(item.get("ordinal", item.get("chunk_index", 0))),
                        content_modality=str(item["content_modality"]),
                        page_number=int(item.get("page_number", 0)),
                        heading=item.get("heading"),
                        object_id=object_id,
                    )
                )

    async def register_objects(self, objects: list[dict[str, Any]]) -> dict[str, str]:
        """Persist idempotent uploaded-object references before chunks are indexed."""
        if not objects:
            return {}
        async with self.database.session_factory.begin() as session:
            if self._settings:
                await _lock_public_quota(session)
            result: dict[str, str] = {}
            for item in objects:
                key = str(item["object_key"])
                existing = await session.scalar(select(DocumentObject).where(DocumentObject.object_key == key))
                if existing:
                    result[key] = str(existing.object_id)
                    continue
                if self._settings:
                    await _check_global_capacity(session, int(item["size_bytes"]), self._settings, new_document=False)
                row = DocumentObject(
                    document_id=_uuid(item["doc_id"]),
                    object_kind=str(item.get("object_kind", "derived")),
                    object_key=key,
                    mime_type=str(item["mime_type"]),
                    size_bytes=int(item["size_bytes"]),
                    sha256=item.get("sha256"),
                    page_number=item.get("page_number"),
                )
                session.add(row)
                await session.flush()
                result[key] = str(row.object_id)
            return result

    async def clear_processing_artifacts(self, doc_id: str) -> None:
        """Remove old vector references and derived catalog rows for a retry."""
        async with self.database.session_factory.begin() as session:
            document_id = _uuid(doc_id)
            await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
            await session.execute(
                delete(DocumentObject).where(
                    and_(DocumentObject.document_id == document_id, DocumentObject.object_kind == "derived")
                )
            )
            await session.execute(
                update(Document)
                .where(Document.doc_id == document_id)
                .values(chunks_count=0, warnings=[], safe_error_message=None, updated_at=datetime.now(UTC))
            )

    async def valid_chunk_refs(self, refs: list[dict[str, str]], visitor_id: str | None = None) -> set[str]:
        """Return only ready chunks whose persisted document/object still matches Pinecone."""
        if not refs:
            return set()
        chunk_ids = [str(item["chunk_id"]) for item in refs]
        expected = {(str(item["chunk_id"]), str(item["doc_id"]), str(item.get("object_key", ""))) for item in refs}
        async with self.database.session_factory() as session:
            query = (
                select(Chunk.chunk_id, Document.doc_id, DocumentObject.object_key)
                .join(Document, Document.doc_id == Chunk.document_id)
                .outerjoin(DocumentObject, DocumentObject.object_id == Chunk.object_id)
                .where(Chunk.chunk_id.in_(chunk_ids), Document.status == "ready", Document.deleted_at.is_(None), _document_owner_clause(visitor_id))
            )
            rows = await session.execute(query)
            return {
                str(chunk_id)
                for chunk_id, document_id, object_key in rows
                if (str(chunk_id), str(document_id), str(object_key or "")) in expected
            }

    async def owned_chunk_ids(self, chunk_ids: list[str], visitor_id: str) -> set[str]:
        """Return chunk IDs whose parent document belongs to one visitor."""
        if not chunk_ids:
            return set()
        async with self.database.session_factory() as session:
            rows = await session.scalars(select(Chunk.chunk_id).join(Document).where(Chunk.chunk_id.in_(chunk_ids), _document_owner_clause(visitor_id), Document.deleted_at.is_(None)))
            return {str(value) for value in rows}

    async def get_vector_ids(self, doc_id: str, visitor_id: str | None = None) -> list[str]:
        """Return Pinecone IDs, which are the chunk IDs in the new schema."""
        async with self.database.session_factory() as session:
            result = await session.scalars(
                select(Chunk.chunk_id).join(Document).where(Chunk.document_id == _uuid(doc_id), _document_owner_clause(visitor_id)).order_by(Chunk.ordinal)
            )
            return list(result)

    async def get_object_keys(self, doc_id: str, visitor_id: str | None = None) -> list[str]:
        """Return every R2 key retained for one document before catalog cleanup."""
        async with self.database.session_factory() as session:
            result = await session.scalars(
                select(DocumentObject.object_key).join(Document).where(DocumentObject.document_id == _uuid(doc_id), _document_owner_clause(visitor_id))
            )
            return list(result)

    async def begin_deletion(self, doc_id: str, lease_seconds: int = 120, visitor_id: str | None = None) -> dict[str, Any]:
        """Claim one deletion and return all durable external-resource identifiers."""
        document_id = _uuid(doc_id)
        now = datetime.now(UTC)
        async with self.database.session_factory.begin() as session:
            document = await session.scalar(select(Document).where(Document.doc_id == document_id, _document_owner_clause(visitor_id)).with_for_update())
            if document is None:
                return {"status": "missing", "claimed": False}
            if document.status == "deleted":
                return {"status": "deleted", "claimed": False}
            if not _deletion_claim_available(document, now):
                return {"status": "deleting", "claimed": False, "stage": document.deletion_stage}
            previous = document.status
            document.status = "deleting"
            document.deletion_stage = document.deletion_stage or "pinecone"
            document.deletion_error = None
            document.deletion_started_at = document.deletion_started_at or now
            document.deletion_lease_until = now + timedelta(seconds=lease_seconds)
            document.updated_at = now
            session.add(
                IngestionEvent(
                    document_id=document_id,
                    event_type="status_transition" if previous != "deleting" else "deletion_retry_claimed",
                    safe_details={"from_status": previous, "to_status": "deleting", "stage": document.deletion_stage},
                )
            )
            chunk_ids = list(await session.scalars(select(Chunk.chunk_id).where(Chunk.document_id == document_id)))
            object_keys = list(
                await session.scalars(select(DocumentObject.object_key).where(DocumentObject.document_id == document_id))
            )
            return {
                "status": "deleting",
                "claimed": True,
                "stage": document.deletion_stage,
                "visitor_id": str(document.visitor_id) if document.visitor_id else None,
                "chunk_ids": chunk_ids,
                "object_keys": object_keys,
            }

    async def mark_deletion_stage(
        self, doc_id: str, stage: str, *, error: str | None = None, lease_seconds: int = 120
    ) -> None:
        """Persist external progress and release the lease when retry is required."""
        if stage not in _DELETION_STAGES:
            raise ValueError("estágio de exclusão inválido")
        now = datetime.now(UTC)
        async with self.database.session_factory.begin() as session:
            document = await session.get(Document, _uuid(doc_id))
            if document is None:
                raise CatalogError("Documento não encontrado")
            if document.status != "deleting":
                return
            document.deletion_stage = stage
            document.deletion_error = error
            document.safe_error_message = error
            document.updated_at = now
            document.deletion_lease_until = None if error else now + timedelta(seconds=lease_seconds)
            session.add(
                IngestionEvent(
                    document_id=document.doc_id,
                    event_type="deletion_failed" if error else "deletion_stage_completed",
                    safe_details={"stage": stage, "retryable": bool(error)},
                )
            )

    async def complete_deletion(self, doc_id: str) -> None:
        """Finalize a deletion only after both external stores were confirmed empty."""
        now = datetime.now(UTC)
        async with self.database.session_factory.begin() as session:
            document = await session.get(Document, _uuid(doc_id))
            if document is None or document.status == "deleted":
                return
            await session.execute(delete(Chunk).where(Chunk.document_id == document.doc_id))
            await session.execute(delete(DocumentObject).where(DocumentObject.document_id == document.doc_id))
            document.status = "deleted"
            document.deleted_at = now
            document.deletion_stage = "completed"
            document.deletion_error = None
            document.safe_error_message = None
            document.deletion_lease_until = None
            document.updated_at = now
            session.add(
                IngestionEvent(
                    document_id=document.doc_id,
                    event_type="document_deleted",
                    safe_details={"stage": "completed"},
                )
            )

    async def list_expired_documents(self, limit: int, older_than: timedelta = timedelta()) -> list[dict[str, Any]]:
        """Return bounded retention-expired or abandoned-upload candidates."""
        now = datetime.now(UTC)
        cutoff = now - older_than

        abandoned_upload = and_(
            Document.status == "pending_upload",
            Document.upload_expires_at.is_not(None),
            Document.upload_expires_at <= now,
        )
        retention_expired = and_(
            Document.status.in_(["uploaded", "processing", "indexing", "ready", "failed"]),
            Document.expires_at.is_not(None),
            Document.expires_at <= now,
        )
        eligible = and_(
            or_(abandoned_upload, retention_expired),
            Document.updated_at <= cutoff,
        )
        return await self._fetch_many(eligible, limit=limit)

    async def list_deleting_documents(self, limit: int) -> list[dict[str, Any]]:
        """Return bounded deletion jobs for recovery workers."""
        return await self._fetch_many(Document.status == "deleting", limit=limit)

    async def list_stuck_documents(self, status: str, older_than: timedelta, limit: int) -> list[str]:
        """Return bounded document IDs that have not advanced recently."""
        cutoff = datetime.now(UTC) - older_than
        rows = await self._fetch_many(and_(Document.status == status, Document.updated_at <= cutoff), limit=limit)
        return [row["doc_id"] for row in rows]

    async def list_all_documents(self) -> list[dict[str, Any]]:
        """Return all catalog documents, including deleted audit-retention rows."""
        return await self._fetch_many(True)

    async def list_documents_without_objects(self) -> list[str]:
        """Find active documents with no durable R2 reference."""
        async with self.database.session_factory() as session:
            query = (
                select(Document.doc_id)
                .outerjoin(DocumentObject, DocumentObject.document_id == Document.doc_id)
                .where(Document.status != "deleted")
                .group_by(Document.doc_id)
                .having(func.count(DocumentObject.object_id) == 0)
            )
            return [str(value) for value in await session.scalars(query)]

    async def list_ready_without_chunks(self) -> list[str]:
        """Find ready documents whose Postgres chunk traceability is empty."""
        async with self.database.session_factory() as session:
            query = (
                select(Document.doc_id)
                .outerjoin(Chunk, Chunk.document_id == Document.doc_id)
                .where(Document.status == "ready")
                .group_by(Document.doc_id)
                .having(func.count(Chunk.chunk_id) == 0)
            )
            return [str(value) for value in await session.scalars(query)]

    async def list_chunk_ids_by_document(self) -> dict[str, list[str]]:
        """Return the Postgres chunk inventory grouped by document."""
        async with self.database.session_factory() as session:
            rows = await session.execute(select(Chunk.document_id, Chunk.chunk_id).order_by(Chunk.ordinal))
            result: dict[str, list[str]] = {}
            for document_id, chunk_id in rows:
                result.setdefault(str(document_id), []).append(str(chunk_id))
            return result

    async def record_audit_event(self, event_type: str, safe_details: Mapping[str, Any] | None = None) -> str:
        """Persist an administrative event without a document foreign key."""
        event_id = uuid4()
        async with self.database.session_factory.begin() as session:
            session.add(AuditEvent(event_id=event_id, event_type=event_type, safe_details=dict(safe_details or {})))
        return str(event_id)

    async def delete_chunks(self, doc_id: str) -> None:
        """Delete vector traceability rows for one document."""
        async with self.database.session_factory.begin() as session:
            await session.execute(delete(Chunk).where(Chunk.document_id == _uuid(doc_id)))

    async def delete_file(self, doc_id: str) -> None:
        """Delete a document and rely on Postgres cascades for its children."""
        async with self.database.session_factory.begin() as session:
            await session.execute(delete(Document).where(Document.doc_id == _uuid(doc_id)))

    async def remove_document(self, doc_id: str) -> None:
        """Remove one document through the repository contract."""
        await self.delete_file(doc_id)

    async def list_files(self, visitor_id: str | None = None) -> list[dict[str, Any]]:
        """List non-deleted documents without binary content or internal paths."""
        return await self._fetch_many(and_(Document.deleted_at.is_(None), _document_owner_clause(visitor_id)))

    async def list_documents(self, visitor_id: str | None = None) -> list[dict[str, Any]]:
        """List documents through the repository contract."""
        return await self.list_files(visitor_id)

    async def stats(self, visitor_id: str | None = None) -> dict[str, Any]:
        """Return lifecycle, retention, byte and persistence metrics."""
        async with self.database.session_factory() as session:
            active = and_(Document.deleted_at.is_(None), _document_owner_clause(visitor_id))
            files = await session.scalar(select(func.count()).select_from(Document).where(Document.status == "ready", active))
            chunks = await session.scalar(select(func.count()).select_from(Chunk).join(Document).where(Document.status == "ready", active))
            rows = await session.execute(
                select(Document.file_type, func.count()).where(Document.status == "ready", active).group_by(Document.file_type)
            )
            status_rows = await session.execute(select(Document.status, func.count()).where(active).group_by(Document.status))
            bytes_registered = await session.scalar(
                select(func.coalesce(func.sum(Document.size_bytes), 0)).where(active)
            )
            persistent_objects = await session.scalar(
                select(func.count())
                .select_from(DocumentObject)
                .join(Document)
                .where(active)
            )
            persistent_object_bytes = await session.scalar(
                select(func.coalesce(func.sum(DocumentObject.size_bytes), 0)).select_from(DocumentObject).join(Document).where(active)
            )
            expired = await session.scalar(
                select(func.count()).select_from(Document).where(active, or_(Document.expires_at <= func.now(), Document.upload_expires_at <= func.now()))
            )
            pending = await session.scalar(select(func.count()).select_from(Document).where(Document.status == "deleting", active))
            return {
                "files": int(files or 0),
                "chunks": int(chunks or 0),
                "by_type": {file_type: count for file_type, count in rows},
                "documents_by_status": {status: count for status, count in status_rows},
                "bytes_registered": int(bytes_registered or 0),
                "persistent_objects": int(persistent_objects or 0),
                "persistent_object_bytes": int(persistent_object_bytes or 0),
                "documents_expired": int(expired or 0),
                "expired_documents": int(expired or 0),
                "deletions_pending": int(pending or 0),
                "pending_deletions": int(pending or 0),
            }

    async def get_statistics(self, visitor_id: str | None = None) -> dict[str, Any]:
        """Return statistics through the repository contract."""
        return await self.stats(visitor_id)

    async def record_feedback(
        self, visitor_id: str, response_id: str, useful: bool
    ) -> str:
        """Upsert feedback only for one visitor-owned persisted response."""
        owner, selected = _visitor_uuid(visitor_id), _uuid(response_id)
        async with self.database.session_factory.begin() as session:
            message = await session.scalar(select(ConversationMessage).where(ConversationMessage.message_id == selected, ConversationMessage.visitor_id == owner).with_for_update())
            if message is None:
                raise FileNotFoundInCatalogError("Resposta não encontrada")
            feedback = await session.scalar(select(Feedback).where(Feedback.visitor_id == owner, Feedback.message_id == selected).with_for_update())
            if feedback is not None:
                feedback.useful = useful
                return str(feedback.feedback_id)
            feedback_id = uuid4()
            session.add(Feedback(feedback_id=feedback_id, visitor_id=owner, message_id=selected, question=message.question, answer=message.answer, useful=useful, source_ids=list(message.source_ids or [])))
        return str(feedback_id)

    async def persist_generated_response(
        self, visitor_id: str, question: str, answer: str, source_ids: list[str], sources: list[dict[str, Any]], insufficient_context: bool, conversation_id: str | None = None
    ) -> dict[str, str]:
        """Atomically persist the conversation container and generated response."""
        owner, selected, message_id = _visitor_uuid(visitor_id), _uuid(conversation_id) if conversation_id else uuid4(), uuid4()
        async with self.database.session_factory.begin() as session:
            conversation = await session.scalar(select(Conversation).where(Conversation.conversation_id == selected, Conversation.visitor_id == owner).with_for_update())
            if conversation_id and conversation is None:
                raise FileNotFoundInCatalogError("Conversa não encontrada")
            if conversation is None:
                conversation = Conversation(conversation_id=selected, visitor_id=owner)
                session.add(conversation)
            session.add(ConversationMessage(message_id=message_id, conversation_id=selected, visitor_id=owner, question=question, answer=answer, source_ids=source_ids, sources=sources, insufficient_context=insufficient_context))
            conversation.updated_at = datetime.now(UTC)
        return {"conversation_id": str(selected), "response_id": str(message_id), "message_id": str(message_id)}

    async def create_conversation(self, visitor_id: str, conversation_id: str | None = None) -> str:
        """Create or verify one visitor-owned conversation."""
        owner = _visitor_uuid(visitor_id)
        selected = _uuid(conversation_id) if conversation_id else uuid4()
        async with self.database.session_factory.begin() as session:
            conversation = await session.scalar(select(Conversation).where(Conversation.conversation_id == selected, Conversation.visitor_id == owner))
            if conversation_id and conversation is None:
                raise FileNotFoundInCatalogError("Conversa nÃ£o encontrada")
            if conversation is None:
                session.add(Conversation(conversation_id=selected, visitor_id=owner))
        return str(selected)

    async def get_conversation(self, visitor_id: str, conversation_id: str) -> list[dict[str, Any]] | None:
        """Return messages only when the conversation belongs to the visitor."""
        try:
            owner, selected = _visitor_uuid(visitor_id), _uuid(conversation_id)
        except ValueError:
            return None
        async with self.database.session_factory() as session:
            exists = await session.scalar(select(Conversation.conversation_id).where(Conversation.conversation_id == selected, Conversation.visitor_id == owner))
            if exists is None:
                return None
            rows = await session.scalars(select(ConversationMessage).where(ConversationMessage.conversation_id == selected, ConversationMessage.visitor_id == owner).order_by(ConversationMessage.created_at))
            return [_message_record(row) for row in rows]

    async def get_message(self, visitor_id: str, message_id: str) -> dict[str, Any] | None:
        """Return one persisted response only inside the visitor scope."""
        try:
            selected, owner = _uuid(message_id), _visitor_uuid(visitor_id)
        except ValueError:
            return None
        async with self.database.session_factory() as session:
            row = await session.scalar(select(ConversationMessage).where(ConversationMessage.message_id == selected, ConversationMessage.visitor_id == owner))
            return _message_record(row) if row else None

    async def record_message(
        self, visitor_id: str, conversation_id: str, question: str, answer: str, source_ids: list[str], sources: list[dict[str, Any]], insufficient_context: bool
    ) -> str:
        """Persist a generated answer and its source snapshot in one transaction."""
        owner, selected, message_id = _visitor_uuid(visitor_id), _uuid(conversation_id), uuid4()
        async with self.database.session_factory.begin() as session:
            conversation = await session.scalar(select(Conversation).where(Conversation.conversation_id == selected, Conversation.visitor_id == owner).with_for_update())
            if conversation is None:
                raise FileNotFoundInCatalogError("Conversa nÃ£o encontrada")
            session.add(ConversationMessage(message_id=message_id, conversation_id=selected, visitor_id=owner, question=question, answer=answer, source_ids=source_ids, sources=sources, insufficient_context=insufficient_context))
            conversation.updated_at = datetime.now(UTC)
        return str(message_id)

    async def delete_conversation(self, visitor_id: str, conversation_id: str) -> bool:
        """Delete one conversation only when it belongs to the visitor."""
        try:
            selected, owner = _uuid(conversation_id), _visitor_uuid(visitor_id)
        except ValueError:
            return False
        async with self.database.session_factory.begin() as session:
            deleted_id = await session.scalar(
                delete(Conversation)
                .where(
                    Conversation.conversation_id == selected,
                    Conversation.visitor_id == owner,
                )
                .returning(Conversation.conversation_id)
            )
        return deleted_id is not None

    async def increment_usage(
        self, client_hash: str, usage_date: date, *, uploads: int = 0, queries: int = 0, bytes_uploaded: int = 0
    ) -> dict[str, Any]:
        """Atomically increment a client-day counter."""
        async with self.database.session_factory.begin() as session:
            statement = (
                insert(UsageCounter)
                .values(
                    client_hash=client_hash,
                    usage_date=usage_date,
                    uploads_count=uploads,
                    queries_count=queries,
                    bytes_uploaded=bytes_uploaded,
                )
                .on_conflict_do_update(
                    index_elements=[UsageCounter.client_hash, UsageCounter.usage_date],
                    set_={
                        "uploads_count": UsageCounter.uploads_count + uploads,
                        "queries_count": UsageCounter.queries_count + queries,
                        "bytes_uploaded": UsageCounter.bytes_uploaded + bytes_uploaded,
                    },
                )
            )
            await session.execute(statement)
        return await self.get_usage(client_hash, usage_date)

    async def reserve_query_quota(self, client_hash: str, usage_date: date) -> dict[str, Any]:
        """Atomically reserve one public query for a client-day."""
        settings = self._settings
        if settings is None:
            raise CatalogError("Configuração de quota pública indisponível")
        async with self.database.session_factory.begin() as session:
            await _lock_public_quota(session)
            counter = await _locked_usage_counter(session, client_hash, usage_date)
            if counter.queries_count + 1 > settings.max_daily_queries_per_client:
                raise QuotaExceededError(
                    "O limite diário de consultas deste cliente foi atingido.",
                    _retry_after_until_next_utc_day(),
                )
            _increment_counter(counter, queries=1)
        return await self.get_usage(client_hash, usage_date)

    async def get_usage(self, client_hash: str, usage_date: date) -> dict[str, Any]:
        """Read one client-day counter."""
        async with self.database.session_factory() as session:
            counter = await session.get(UsageCounter, (client_hash, usage_date))
            return {
                "client_hash": client_hash,
                "usage_date": usage_date,
                "uploads_count": counter.uploads_count if counter else 0,
                "queries_count": counter.queries_count if counter else 0,
                "bytes_uploaded": counter.bytes_uploaded if counter else 0,
            }

    async def record_ingestion_event(
        self, event_type: str, document_id: str, safe_details: Mapping[str, Any] | None = None
    ) -> str:
        """Store a JSON-safe operational event."""
        event_id = uuid4()
        async with self.database.session_factory.begin() as session:
            session.add(
                IngestionEvent(
                    event_id=event_id,
                    document_id=_uuid(document_id),
                    event_type=event_type,
                    safe_details=dict(safe_details or {}),
                )
            )
        return str(event_id)

    async def clear_catalog(self) -> None:
        """Clear catalog data in an explicit transaction."""
        async with self.database.session_factory.begin() as session:
            await session.execute(delete(IngestionEvent))
            await session.execute(delete(UsageCounter))
            await session.execute(delete(Feedback))
            await session.execute(delete(ConversationMessage))
            await session.execute(delete(Conversation))
            await session.execute(delete(Document))

    async def _fetch_one(self, condition: Any) -> dict[str, Any] | None:
        """Fetch one document and its original object."""
        async with self.database.session_factory() as session:
            result = await session.execute(self._document_query().where(condition))
            row = result.first()
            return _record(*row) if row else None

    async def _conditional_status(self, doc_id: str, current: str, target: str, visitor_id: str | None = None) -> bool:
        """Apply a status transition only if the document still has the expected state."""
        async with self.database.session_factory.begin() as session:
            updated_doc_id = await session.scalar(
                update(Document)
                .where(
                    Document.doc_id == _uuid(doc_id),
                    Document.status == current,
                    _document_owner_clause(visitor_id),
                )
                .values(status=target, updated_at=datetime.now(UTC))
                .returning(Document.doc_id)
            )
            if updated_doc_id is not None:
                session.add(
                    IngestionEvent(
                        document_id=updated_doc_id,
                        event_type="status_transition",
                        safe_details={"from_status": current, "to_status": target},
                    )
                )
        return updated_doc_id is not None

    async def _fetch_many(self, condition: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Fetch documents ordered newest first."""
        async with self.database.session_factory() as session:
            query = self._document_query().where(condition).order_by(Document.created_at.desc())
            if limit is not None:
                query = query.limit(limit)
            result = await session.execute(query)
            return [_record(*row) for row in result.all()]

    def _retention_days(self) -> int:
        """Read the configured public retention policy."""
        return self._settings.public_retention_days if self._settings else 0

    def _document_query(self):
        """Build the compatibility projection query."""
        return select(Document, DocumentObject).outerjoin(
            DocumentObject,
            and_(DocumentObject.document_id == Document.doc_id, DocumentObject.object_kind == "original"),
        )

    async def _derived_object(self, session, item: Mapping[str, Any], cache: dict[str, DocumentObject]) -> UUID | None:
        """Resolve or create one derived object reference."""
        key = str(item.get("media_key", ""))
        if not key:
            return None
        if key in cache:
            return cache[key].object_id
        result = await session.scalar(select(DocumentObject).where(DocumentObject.object_key == key))
        object_row = result or DocumentObject(
            document_id=_uuid(item["doc_id"]),
            object_kind="derived",
            object_key=key,
            mime_type=str(item.get("mime_type") or "application/octet-stream"),
            size_bytes=int(item.get("size_bytes", 0)),
            sha256=item.get("sha256"),
            page_number=item.get("page_number"),
        )
        if result is None:
            session.add(object_row)
        cache[key] = object_row
        await session.flush()
        return object_row.object_id


def _is_uuid(value: str) -> bool:
    """Return whether a string is a UUID."""
    try:
        UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def _document_values(record: Mapping[str, Any], *, retention_days: int = 0) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize a public record into document and original-object values."""
    doc_id = _uuid(record.get("doc_id"), fallback=uuid4())
    sanitized = str(record.get("sanitized_name", record.get("stored_name", record.get("original_name", "upload"))))
    object_key = str(record.get("object_key", record.get("storage_key", "")))
    sha256 = str(record.get("sha256", record.get("doc_id", "")))
    values = {
        "doc_id": doc_id,
        "original_name": str(record["original_name"]),
        "sanitized_name": sanitized,
        "file_type": str(record["file_type"]),
        "mime_type": str(record["mime_type"]),
        "sha256": sha256,
        "size_bytes": int(record["size_bytes"]),
        "status": str(record.get("status", "processing")),
        "chunks_count": int(record.get("chunks_count", 0)),
        "warnings": _warnings(record.get("warnings", [])),
        "safe_error_message": record.get("safe_error_message", record.get("error_message")),
    }
    if record.get("visitor_id"):
        values["visitor_id"] = _visitor_uuid(record["visitor_id"])
    created_at = _datetime(record.get("created_at"))
    if created_at is not None:
        values["created_at"] = created_at
    updated_at = _datetime(record.get("updated_at"))
    if updated_at is not None:
        values["updated_at"] = updated_at
    expires_at = _datetime(record.get("expires_at")) or _retention_expiry(retention_days)
    if expires_at is not None:
        values["expires_at"] = expires_at
    upload_expires_at = _datetime(record.get("upload_expires_at"))
    if upload_expires_at is not None:
        values["upload_expires_at"] = upload_expires_at
    return (
        values,
        {
            "object_kind": "original",
            "object_key": object_key,
            "mime_type": str(record["mime_type"]),
            "size_bytes": int(record["size_bytes"]),
            "sha256": sha256,
            "page_number": None,
        },
    )


def _retention_expiry(retention_days: int) -> datetime | None:
    """Return a retention deadline or disable expiration when configured as zero."""
    return datetime.now(UTC) + timedelta(days=retention_days) if retention_days > 0 else None


async def _lock_public_quota(session) -> None:
    """Serialize global public reservations across application workers."""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock_name))"), {"lock_name": "rag-public-quota"})


async def _locked_usage_counter(session, client_hash: str, usage_date: date) -> UsageCounter:
    """Load or create a client-day counter while the quota lock is held."""
    counter = await session.get(UsageCounter, (client_hash, usage_date), with_for_update=True)
    if counter is None:
        counter = UsageCounter(client_hash=client_hash, usage_date=usage_date)
        session.add(counter)
        await session.flush()
    return counter


def _check_daily_upload_quota(counter: UsageCounter, settings: Settings) -> None:
    """Reject a reservation before it can create a pending document."""
    if counter.uploads_count + 1 > settings.max_daily_uploads_per_client:
        raise QuotaExceededError("O limite diário de uploads deste cliente foi atingido.", _retry_after_until_next_utc_day())


async def _check_global_capacity(session, size_bytes: int, settings: Settings, *, new_document: bool = True) -> None:
    """Check active document and registered source-byte capacity under the lock."""
    active = await session.scalar(select(func.count()).select_from(Document).where(Document.deleted_at.is_(None)))
    stored = await session.scalar(
        select(func.coalesce(func.sum(DocumentObject.size_bytes), 0))
        .select_from(DocumentObject)
        .join(Document)
        .where(Document.deleted_at.is_(None))
    )
    if new_document and int(active or 0) >= settings.max_active_documents:
        raise CapacityExceededError("A capacidade pública de documentos foi atingida. Tente novamente mais tarde.")
    if int(stored or 0) + size_bytes > settings.max_total_stored_bytes:
        raise CapacityExceededError("A capacidade pública de armazenamento foi atingida. Tente novamente mais tarde.")


def _increment_counter(counter: UsageCounter, *, uploads: int = 0, queries: int = 0, bytes_uploaded: int = 0) -> None:
    """Increment only server-owned usage values."""
    counter.uploads_count += uploads
    counter.queries_count += queries
    counter.bytes_uploaded += bytes_uploaded


def _retry_after_until_next_utc_day() -> int:
    """Calculate the next UTC reset without exposing a timestamp."""
    current = datetime.now(UTC)
    tomorrow = datetime.combine(current.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    return max(1, int((tomorrow - current).total_seconds()))


def _deletion_claim_available(document: Document, now: datetime) -> bool:
    """Allow first claims and retries after an error or expired worker lease."""
    if document.status != "deleting":
        return True
    if document.deletion_error:
        return True
    return not document.deletion_lease_until or document.deletion_lease_until <= now


def _visitor_uuid(value: Any) -> UUID:
    """Parse only a UUIDv4 visitor identifier for database ownership checks."""
    parsed = parse_visitor_id(value)
    if parsed is None:
        raise ValueError("visitor_id must be a UUIDv4")
    return UUID(parsed)


def _record_visitor(values: Mapping[str, Any]) -> str | None:
    """Read a normalized visitor UUID from document values."""
    value = values.get("visitor_id")
    return str(value) if value else None


def _document_owner_clause(visitor_id: str | None) -> ColumnElement[bool]:
    """Return a safe owner predicate or a SQL true clause for maintenance jobs."""
    if visitor_id:
        return Document.visitor_id == _visitor_uuid(visitor_id)
    return true()


def _with_document_owner(
    condition: ColumnElement[bool],
    visitor_id: str | None,
) -> ColumnElement[bool]:
    """Combine one document condition with the optional server-owned visitor scope."""
    return and_(condition, _document_owner_clause(visitor_id))


def _message_record(message: ConversationMessage) -> dict[str, Any]:
    """Map one persisted response to a JSON-safe conversation contract."""
    response_id = str(message.message_id)
    return {"message_id": response_id, "response_id": response_id, "conversation_id": str(message.conversation_id), "question": message.question, "answer": message.answer, "source_ids": list(message.source_ids or []), "sources": list(message.sources or []), "insufficient_context": bool(message.insufficient_context), "created_at": message.created_at.isoformat() if message.created_at else None}
