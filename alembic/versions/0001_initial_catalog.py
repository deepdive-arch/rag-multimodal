"""Create the Postgres catalog schema."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial_catalog"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create all catalog tables and required indexes."""
    utc_default = sa.text("CURRENT_TIMESTAMP")
    op.create_table("documents", sa.Column("doc_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("original_name", sa.String(length=512), nullable=False), sa.Column("sanitized_name", sa.String(length=512), nullable=False), sa.Column("file_type", sa.String(length=32), nullable=False), sa.Column("mime_type", sa.String(length=255), nullable=False), sa.Column("sha256", sa.String(length=64), nullable=False), sa.Column("size_bytes", sa.BigInteger(), nullable=False), sa.Column("status", sa.String(length=32), nullable=False, server_default="pending_upload"), sa.Column("chunks_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")), sa.Column("safe_error_message", sa.Text(), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_default), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_default), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True), sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True), sa.CheckConstraint("status IN ('pending_upload', 'uploaded', 'processing', 'indexing', 'ready', 'failed', 'deleting')", name="ck_documents_status"), sa.PrimaryKeyConstraint("doc_id"))
    op.create_index("ix_documents_sha256", "documents", ["sha256"], unique=True)
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_created_at", "documents", ["created_at"])
    op.create_index("ix_documents_expires_at", "documents", ["expires_at"])
    op.create_table("document_objects", sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("object_kind", sa.String(length=16), nullable=False), sa.Column("object_key", sa.String(length=1024), nullable=False), sa.Column("mime_type", sa.String(length=255), nullable=False), sa.Column("size_bytes", sa.BigInteger(), nullable=False), sa.Column("sha256", sa.String(length=64), nullable=True), sa.Column("page_number", sa.Integer(), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_default), sa.CheckConstraint("object_kind IN ('original', 'derived')", name="ck_document_objects_kind"), sa.ForeignKeyConstraint(["document_id"], ["documents.doc_id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("object_id"), sa.UniqueConstraint("object_key"))
    op.create_index("ix_document_objects_document_id", "document_objects", ["document_id"])
    op.create_table("chunks", sa.Column("chunk_id", sa.String(length=255), nullable=False), sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("ordinal", sa.Integer(), nullable=False), sa.Column("content_modality", sa.String(length=32), nullable=False), sa.Column("page_number", sa.Integer(), nullable=False, server_default="0"), sa.Column("heading", sa.String(length=512), nullable=True), sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_default), sa.ForeignKeyConstraint(["document_id"], ["documents.doc_id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["object_id"], ["document_objects.object_id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("chunk_id"))
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_object_id", "chunks", ["object_id"])
    op.create_table("feedback", sa.Column("feedback_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("question", sa.Text(), nullable=False), sa.Column("answer", sa.Text(), nullable=False), sa.Column("useful", sa.Boolean(), nullable=False), sa.Column("source_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_default), sa.PrimaryKeyConstraint("feedback_id"))
    op.create_table("usage_counters", sa.Column("client_hash", sa.String(length=128), nullable=False), sa.Column("usage_date", sa.Date(), nullable=False), sa.Column("uploads_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("queries_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("bytes_uploaded", sa.BigInteger(), nullable=False, server_default="0"), sa.PrimaryKeyConstraint("client_hash", "usage_date"), sa.UniqueConstraint("client_hash", "usage_date", name="uq_usage_counters_client_date"))
    op.create_index("ix_usage_counters_client_date", "usage_counters", ["client_hash", "usage_date"])
    op.create_table("ingestion_events", sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("event_type", sa.String(length=64), nullable=False), sa.Column("safe_details", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_default), sa.ForeignKeyConstraint(["document_id"], ["documents.doc_id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("event_id"))
    op.create_index("ix_ingestion_events_document_id", "ingestion_events", ["document_id"])


def downgrade() -> None:
    """Drop the catalog in dependency order."""
    op.drop_index("ix_ingestion_events_document_id", table_name="ingestion_events")
    op.drop_table("ingestion_events")
    op.drop_index("ix_usage_counters_client_date", table_name="usage_counters")
    op.drop_table("usage_counters")
    op.drop_table("feedback")
    op.drop_index("ix_chunks_object_id", table_name="chunks")
    op.drop_index("ix_chunks_document_id", table_name="chunks")
    op.drop_table("chunks")
    op.drop_index("ix_document_objects_document_id", table_name="document_objects")
    op.drop_table("document_objects")
    op.drop_index("ix_documents_expires_at", table_name="documents")
    op.drop_index("ix_documents_created_at", table_name="documents")
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_index("ix_documents_sha256", table_name="documents")
    op.drop_table("documents")
