"""Remove Supabase Data API privileges from the backend-only catalog."""

from alembic import op


revision = "0004_revoke_data_api"
down_revision = "0003_deletion_recovery"
branch_labels = None
depends_on = None


_ROLES = ("anon", "authenticated")
_TABLES = (
    "alembic_version",
    "audit_events",
    "chunks",
    "document_objects",
    "documents",
    "feedback",
    "ingestion_events",
    "usage_counters",
)


def upgrade() -> None:
    """Keep catalog access restricted to the direct Postgres backend role."""
    for role in _ROLES:
        _change_privileges("REVOKE ALL PRIVILEGES ON TABLE", "FROM", role)


def downgrade() -> None:
    """Restore Supabase's broad default table grants when explicitly rolled back."""
    for role in reversed(_ROLES):
        _change_privileges("GRANT ALL PRIVILEGES ON TABLE", "TO", role)


def _change_privileges(action: str, preposition: str, role: str) -> None:
    """Apply a static grant change only when the Supabase role exists."""
    tables = ", ".join(_TABLES)
    op.execute(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN {action} {tables} {preposition} {role}; END IF; END $$")
