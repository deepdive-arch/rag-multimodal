"""Enforce conversation and feedback ownership relationships in Postgres."""

from alembic import op


revision = "0007_response_ownership_constraints"
down_revision = "0006_feedback_response_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Remove orphan feedback and enforce composite visitor ownership."""
    _prepare_upgrade()
    _enforce_upgrade()


def _prepare_upgrade() -> None:
    """Remove the legacy relationships and rows that cannot be owned."""
    _drop_legacy_constraints()
    _make_feedback_required()


def _drop_legacy_constraints() -> None:
    """Drop relationships that do not include visitor ownership."""
    op.drop_constraint("fk_feedback_message_id", "feedback", type_="foreignkey")
    op.drop_constraint("messages_conversation_id_fkey", "messages", type_="foreignkey")


def _make_feedback_required() -> None:
    """Remove unowned feedback and make both ownership columns mandatory."""
    op.execute("DELETE FROM feedback WHERE visitor_id IS NULL OR message_id IS NULL")
    _require_feedback_columns()


def _require_feedback_columns() -> None:
    """Make the feedback owner and response reference non-nullable."""
    op.alter_column("feedback", "visitor_id", nullable=False)
    op.alter_column("feedback", "message_id", nullable=False)


def _enforce_upgrade() -> None:
    """Create database-level composite ownership constraints."""
    _add_unique_ownership()
    _add_composite_foreign_keys()


def _add_unique_ownership() -> None:
    """Expose composite keys for ownership-aware foreign keys."""
    op.create_unique_constraint("uq_conversations_id_visitor", "conversations", ["conversation_id", "visitor_id"])
    op.create_unique_constraint("uq_messages_id_visitor", "messages", ["message_id", "visitor_id"])


def _add_composite_foreign_keys() -> None:
    """Require the same visitor across conversation, message, and feedback."""
    op.create_foreign_key("fk_messages_conversation_visitor", "messages", "conversations", ["conversation_id", "visitor_id"], ["conversation_id", "visitor_id"], ondelete="CASCADE")
    op.create_foreign_key("fk_feedback_message_visitor", "feedback", "messages", ["message_id", "visitor_id"], ["message_id", "visitor_id"], ondelete="CASCADE")


def downgrade() -> None:
    """Restore nullable legacy feedback and single-column foreign keys."""
    _remove_composite_ownership()
    _restore_legacy_relationships()


def _remove_composite_ownership() -> None:
    """Drop the ownership-aware relationships and supporting unique keys."""
    _drop_composite_foreign_keys()
    _drop_unique_ownership()


def _drop_composite_foreign_keys() -> None:
    """Drop composite relationships before their unique keys."""
    op.drop_constraint("fk_feedback_message_visitor", "feedback", type_="foreignkey")
    op.drop_constraint("fk_messages_conversation_visitor", "messages", type_="foreignkey")


def _drop_unique_ownership() -> None:
    """Drop composite keys after dependent relationships are gone."""
    op.drop_constraint("uq_messages_id_visitor", "messages", type_="unique")
    op.drop_constraint("uq_conversations_id_visitor", "conversations", type_="unique")


def _restore_legacy_relationships() -> None:
    """Restore nullable columns and the former single-column relationships."""
    _restore_nullable_feedback()
    _restore_single_foreign_keys()


def _restore_nullable_feedback() -> None:
    """Restore the pre-migration feedback nullability."""
    op.alter_column("feedback", "message_id", nullable=True)
    op.alter_column("feedback", "visitor_id", nullable=True)


def _restore_single_foreign_keys() -> None:
    """Restore relationships that do not include visitor ownership."""
    op.create_foreign_key("messages_conversation_id_fkey", "messages", "conversations", ["conversation_id"], ["conversation_id"], ondelete="CASCADE")
    op.create_foreign_key("fk_feedback_message_id", "feedback", "messages", ["message_id"], ["message_id"], ondelete="SET NULL")
