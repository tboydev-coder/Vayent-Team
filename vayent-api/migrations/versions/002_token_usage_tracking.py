"""Add plan-based token tracking support.

Revision ID: 002_token_usage_tracking
Revises: 001_initial
Create Date: 2026-03-22 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "002_token_usage_tracking"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("plan_type", sa.String(length=20), nullable=False, server_default=sa.text("'free'")),
    )
    op.add_column(
        "users",
        sa.Column("monthly_token_usage", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "users",
        sa.Column("reserved_token_usage", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "users",
        sa.Column("token_reset_date", sa.Date(), nullable=False, server_default=sa.text("CURRENT_DATE")),
    )

    op.execute(
        """
        UPDATE users
        SET plan_type = CASE
            WHEN is_premium = TRUE THEN 'paid'
            ELSE 'free'
        END
        """
    )

    op.create_table(
        "token_usage_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("message_id", sa.String(length=36), nullable=True),
        sa.Column("request_kind", sa.String(length=50), nullable=False, server_default=sa.text("'chat'")),
        sa.Column("tokens_used", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_token_usage_logs_user_id"), "token_usage_logs", ["user_id"], unique=False)
    op.create_index(op.f("ix_token_usage_logs_session_id"), "token_usage_logs", ["session_id"], unique=False)
    op.create_index(op.f("ix_token_usage_logs_message_id"), "token_usage_logs", ["message_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_token_usage_logs_message_id"), table_name="token_usage_logs")
    op.drop_index(op.f("ix_token_usage_logs_session_id"), table_name="token_usage_logs")
    op.drop_index(op.f("ix_token_usage_logs_user_id"), table_name="token_usage_logs")
    op.drop_table("token_usage_logs")
    op.drop_column("users", "token_reset_date")
    op.drop_column("users", "reserved_token_usage")
    op.drop_column("users", "monthly_token_usage")
    op.drop_column("users", "plan_type")
