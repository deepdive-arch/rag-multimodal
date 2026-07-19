"""Protect the internal catalog when Supabase exposes the public schema."""

from alembic import op


revision = "0002_enable_catalog_rls"
down_revision = "0001_initial_catalog"
branch_labels = None
depends_on = None


_TABLES = ("documents", "document_objects", "chunks", "feedback", "usage_counters", "ingestion_events")


def upgrade() -> None:
    """Enable RLS without adding anonymous or authenticated access policies."""
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Restore the pre-RLS state for local rollback."""
    for table in reversed(_TABLES):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
