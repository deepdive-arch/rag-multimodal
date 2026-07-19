"""Add retention, deletion recovery state and global audit events."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_deletion_recovery"
down_revision = "0002_enable_catalog_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Store retry state without removing external-resource identifiers."""
    op.execute(
        "ALTER TABLE documents DROP CONSTRAINT ck_documents_status"
    )
    op.create_check_constraint(
        "ck_documents_status",
        "documents",
        "status IN ('pending_upload', 'uploaded', 'processing', 'indexing', 'ready', 'failed', 'deleting', 'deleted')",
    )
    op.add_column("documents", sa.Column("upload_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("deletion_stage", sa.String(length=32), nullable=True))
    op.add_column("documents", sa.Column("deletion_error", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("deletion_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("deletion_lease_until", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_documents_upload_expires_at", "documents", ["upload_expires_at"])
    op.create_index("ix_documents_deletion_stage", "documents", ["status", "deletion_stage", "updated_at"])
    op.create_table(
        "audit_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("safe_details", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Remove recovery metadata while restoring the previous status contract."""
    op.execute("ALTER TABLE audit_events DISABLE ROW LEVEL SECURITY")
    op.drop_table("audit_events")
    op.drop_index("ix_documents_deletion_stage", table_name="documents")
    op.drop_index("ix_documents_upload_expires_at", table_name="documents")
    op.drop_column("documents", "deletion_lease_until")
    op.drop_column("documents", "deletion_started_at")
    op.drop_column("documents", "deletion_error")
    op.drop_column("documents", "deletion_stage")
    op.drop_column("documents", "upload_expires_at")
    op.drop_constraint("ck_documents_status", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_status",
        "documents",
        "status IN ('pending_upload', 'uploaded', 'processing', 'indexing', 'ready', 'failed', 'deleting')",
    )
