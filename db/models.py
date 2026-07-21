"""SQLAlchemy models for the Postgres catalog."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative metadata root."""


class Document(Base):
    """One uploaded document and its processing state."""

    __tablename__ = "documents"
    doc_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    visitor_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True, index=True)
    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
    sanitized_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_upload")
    chunks_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    safe_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    upload_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deletion_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    deletion_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('pending_upload', 'uploaded', 'processing', 'indexing', 'ready', 'failed', 'deleting', 'deleted')", name="ck_documents_status"),
        Index("ix_documents_sha256", "sha256"),
        Index(
            "uq_documents_visitor_sha256_active",
            "visitor_id",
            "sha256",
            unique=True,
            postgresql_where=text("status <> 'deleted'"),
        ),
        Index("ix_documents_status", "status"),
        Index("ix_documents_created_at", "created_at"),
        Index("ix_documents_expires_at", "expires_at"),
    )


class DocumentObject(Base):
    """Reference to an original or derived object in external storage."""

    __tablename__ = "document_objects"
    object_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False)
    object_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("object_kind IN ('original', 'derived')", name="ck_document_objects_kind"),
        Index("ix_document_objects_document_id", "document_id"),
    )


class Chunk(Base):
    """Traceability row for a vector stored in Pinecone."""

    __tablename__ = "chunks"
    chunk_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    document_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content_modality: Mapped[str] = mapped_column(String(32), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    heading: Mapped[str | None] = mapped_column(String(512), nullable=True)
    object_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), ForeignKey("document_objects.object_id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (Index("ix_chunks_document_id", "document_id"), Index("ix_chunks_object_id", "object_id"))


class Conversation(Base):
    """A visitor-owned conversation container."""

    __tablename__ = "conversations"
    conversation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    visitor_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("conversation_id", "visitor_id", name="uq_conversations_id_visitor"),)


class ConversationMessage(Base):
    """Persisted answer and source snapshot for one visitor query."""

    __tablename__ = "messages"
    message_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    visitor_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    source_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    insufficient_context: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(["conversation_id", "visitor_id"], ["conversations.conversation_id", "conversations.visitor_id"], ondelete="CASCADE", name="fk_messages_conversation_visitor"),
        UniqueConstraint("message_id", "visitor_id", name="uq_messages_id_visitor"),
        Index("ix_messages_conversation_created_at", "conversation_id", "created_at"),
    )


class Feedback(Base):
    """User feedback for a generated answer."""

    __tablename__ = "feedback"
    feedback_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    visitor_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False, index=True)
    message_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    useful: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(["message_id", "visitor_id"], ["messages.message_id", "messages.visitor_id"], ondelete="CASCADE", name="fk_feedback_message_visitor"),
        UniqueConstraint("visitor_id", "message_id", name="uq_feedback_visitor_message"),
    )


class UsageCounter(Base):
    """Per-client daily usage counters."""

    __tablename__ = "usage_counters"
    client_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    uploads_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queries_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_uploaded: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (Index("ix_usage_counters_client_date", "client_hash", "usage_date"), UniqueConstraint("client_hash", "usage_date", name="uq_usage_counters_client_date"))


class IngestionEvent(Base):
    """Safe operational event for document ingestion."""

    __tablename__ = "ingestion_events"
    event_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (Index("ix_ingestion_events_document_id", "document_id"),)


class AuditEvent(Base):
    """Global administrative audit event that survives catalog cleanup."""

    __tablename__ = "audit_events"
    event_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
