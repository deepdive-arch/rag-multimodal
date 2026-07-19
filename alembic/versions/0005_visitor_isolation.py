"""Add anonymous visitor ownership and persisted conversation responses."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0005_visitor_isolation"
down_revision = "0004_revoke_data_api"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable ownership for legacy rows and strict ownership for new data."""
    op.add_column("documents", sa.Column("visitor_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.drop_index("ix_documents_sha256", table_name="documents")
    op.create_index("ix_documents_sha256", "documents", ["sha256"])
    op.create_index("ix_documents_visitor_id", "documents", ["visitor_id"])
    op.create_index("uq_documents_visitor_sha256", "documents", ["visitor_id", "sha256"], unique=True)
    op.add_column("feedback", sa.Column("visitor_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("feedback", sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_feedback_visitor_id", "feedback", ["visitor_id"])
    op.create_index("ix_feedback_message_id", "feedback", ["message_id"])
    op.create_table(
        "conversations",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visitor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.create_index("ix_conversations_visitor_id", "conversations", ["visitor_id"])
    op.create_table(
        "messages",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visitor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("source_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("insufficient_context", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("message_id"),
    )
    op.create_index("ix_messages_conversation_created_at", "messages", ["conversation_id", "created_at"])
    op.create_index("ix_messages_visitor_id", "messages", ["visitor_id"])
    op.create_foreign_key("fk_feedback_message_id", "feedback", "messages", ["message_id"], ["message_id"], ondelete="SET NULL")
    for table in ("conversations", "messages"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    for role in ("anon", "authenticated"):
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE conversations, messages FROM {role}")


def downgrade() -> None:
    """Remove visitor ownership and conversation persistence."""
    op.drop_constraint("fk_feedback_message_id", "feedback", type_="foreignkey")
    op.drop_index("ix_messages_visitor_id", table_name="messages")
    op.drop_index("ix_messages_conversation_created_at", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_visitor_id", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_feedback_message_id", table_name="feedback")
    op.drop_index("ix_feedback_visitor_id", table_name="feedback")
    op.drop_column("feedback", "message_id")
    op.drop_column("feedback", "visitor_id")
    op.drop_index("uq_documents_visitor_sha256", table_name="documents")
    op.drop_index("ix_documents_visitor_id", table_name="documents")
    op.drop_index("ix_documents_sha256", table_name="documents")
    op.drop_column("documents", "visitor_id")
    op.create_index("ix_documents_sha256", "documents", ["sha256"], unique=True)
