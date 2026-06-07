"""Add durable schema annotations.

Revision ID: 003_schema_annotations
Revises: 002_token_usage_tracking
Create Date: 2026-04-20 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "003_schema_annotations"
down_revision = "002_token_usage_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schema_annotations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("table_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("column_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("nickname", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["connection_id"], ["database_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_schema_annotations_connection_id"),
        "schema_annotations",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        "uq_schema_annotations_target",
        "schema_annotations",
        ["connection_id", "target_type", "table_name", "column_name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_schema_annotations_target", table_name="schema_annotations")
    op.drop_index(op.f("ix_schema_annotations_connection_id"), table_name="schema_annotations")
    op.drop_table("schema_annotations")
