"""Make visitor feedback idempotent per persisted generated response."""

from alembic import op


revision = "0006_feedback_response_ownership"
down_revision = "0005_visitor_isolation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep the latest keyed feedback before enforcing one row per response."""
    op.execute(
        """
        DELETE FROM feedback AS older
        USING feedback AS newer
        WHERE older.visitor_id IS NOT NULL
          AND older.message_id IS NOT NULL
          AND older.visitor_id = newer.visitor_id
          AND older.message_id = newer.message_id
          AND (
            older.created_at < newer.created_at
            OR (older.created_at = newer.created_at AND older.feedback_id < newer.feedback_id)
          )
        """
    )
    op.create_unique_constraint("uq_feedback_visitor_message", "feedback", ["visitor_id", "message_id"])


def downgrade() -> None:
    """Remove the response-scoped feedback uniqueness constraint."""
    op.drop_constraint("uq_feedback_visitor_message", "feedback", type_="unique")
