"""Add spreadsheet source connections.

Revision ID: 007_spreadsheet_sources
Revises: 006_database_connection_ssl_mode
Create Date: 2026-06-03 00:00:00.000000
"""
from alembic import op


revision = "007_spreadsheet_sources"
down_revision = "006_database_connection_ssl_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS spreadsheet_sources (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            source_kind VARCHAR(20) NOT NULL,
            file_type VARCHAR(20) NOT NULL,
            original_filename VARCHAR(255),
            source_url TEXT,
            source_provider VARCHAR(80),
            status VARCHAR(40) NOT NULL DEFAULT 'connected',
            status_message TEXT,
            raw_schema_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            dataset_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            analysis_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            last_synced_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spreadsheet_sources_user_id "
        "ON spreadsheet_sources(user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spreadsheet_sources_status "
        "ON spreadsheet_sources(status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spreadsheet_sources_is_active "
        "ON spreadsheet_sources(is_active)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_spreadsheet_sources_is_active")
    op.execute("DROP INDEX IF EXISTS ix_spreadsheet_sources_status")
    op.execute("DROP INDEX IF EXISTS ix_spreadsheet_sources_user_id")
    op.execute("DROP TABLE IF EXISTS spreadsheet_sources")
