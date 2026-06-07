"""Scope destructive-query confirmations to a database connection.

Revision ID: 005_query_confirmation_connection_scope
Revises: 004_admin_dashboard
Create Date: 2026-05-08 00:00:00.000000
"""
from alembic import op

revision = "005_query_confirmation_connection_scope"
down_revision = "004_admin_dashboard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE query_confirmations
        ADD COLUMN IF NOT EXISTS connection_id VARCHAR(36)
        REFERENCES database_connections(id) ON DELETE CASCADE
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_query_confirmations_connection_id "
        "ON query_confirmations(connection_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_query_confirmations_connection_id")
    op.execute("ALTER TABLE query_confirmations DROP COLUMN IF EXISTS connection_id")
