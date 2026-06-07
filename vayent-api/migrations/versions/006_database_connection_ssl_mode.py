"""Add SSL mode to saved database connections.

Revision ID: 006_database_connection_ssl_mode
Revises: 005_query_confirmation_connection_scope
Create Date: 2026-05-22 00:00:00.000000

"""
from alembic import op


revision = "006_database_connection_ssl_mode"
down_revision = "005_query_confirmation_connection_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE IF EXISTS database_connections
        ADD COLUMN IF NOT EXISTS ssl_mode VARCHAR(20)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE IF EXISTS database_connections
        DROP COLUMN IF EXISTS ssl_mode
        """
    )
