"""Add generic source fields to copilot artifacts.

Revision ID: 008_copilot_artifact_sources
Revises: 007_spreadsheet_sources
Create Date: 2026-06-03 00:00:00.000000
"""
from alembic import op


revision = "008_copilot_artifact_sources"
down_revision = "007_spreadsheet_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE copilot_artifacts ADD COLUMN IF NOT EXISTS source_id VARCHAR(36)")
    op.execute("ALTER TABLE copilot_artifacts ADD COLUMN IF NOT EXISTS source_type VARCHAR(30)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_copilot_artifacts_source_id "
        "ON copilot_artifacts(source_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_copilot_artifacts_source_type "
        "ON copilot_artifacts(source_type)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_copilot_artifacts_source_type")
    op.execute("DROP INDEX IF EXISTS ix_copilot_artifacts_source_id")
    op.execute("ALTER TABLE copilot_artifacts DROP COLUMN IF EXISTS source_type")
    op.execute("ALTER TABLE copilot_artifacts DROP COLUMN IF EXISTS source_id")
