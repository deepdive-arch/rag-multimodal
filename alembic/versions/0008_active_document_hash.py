"""Allow reuse of hashes from deleted documents."""

from alembic import op
import sqlalchemy as sa


revision = "0008_active_document_hash"
down_revision = "0007_response_owner_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Replace global visitor/hash uniqueness with active-row uniqueness."""
    op.drop_index(
        "uq_documents_visitor_sha256",
        table_name="documents",
    )
    op.create_index(
        "uq_documents_visitor_sha256_active",
        "documents",
        ["visitor_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("status <> 'deleted'"),
    )


def downgrade() -> None:
    """Restore uniqueness across active and deleted documents."""
    op.drop_index(
        "uq_documents_visitor_sha256_active",
        table_name="documents",
    )
    op.create_index(
        "uq_documents_visitor_sha256",
        "documents",
        ["visitor_id", "sha256"],
        unique=True,
    )
