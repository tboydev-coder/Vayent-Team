"""Admin dashboard models and user roles.

Revision ID: 004_admin_dashboard
Revises: 003_schema_annotations
Create Date: 2026-05-01 00:00:00.000000
"""
from alembic import op

revision = "004_admin_dashboard"
down_revision = "003_schema_annotations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_suspended BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_super_admin BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS manual_token_balance INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS admin_notes TEXT")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_logs (
            id VARCHAR(36) PRIMARY KEY,
            actor_user_id VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
            actor_username VARCHAR(255),
            actor_email VARCHAR(255),
            action VARCHAR(120) NOT NULL,
            status VARCHAR(40) NOT NULL DEFAULT 'success',
            severity VARCHAR(20) NOT NULL DEFAULT 'info',
            resource_type VARCHAR(80),
            resource_id VARCHAR(255),
            endpoint VARCHAR(255),
            method VARCHAR(12),
            ip_address VARCHAR(80),
            user_agent TEXT,
            request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            response_status_code INTEGER,
            response_time_ms INTEGER,
            error_trace TEXT,
            session_id VARCHAR(255),
            geo_location VARCHAR(255),
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_activity_logs_actor_user_id ON activity_logs(actor_user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_activity_logs_created_at ON activity_logs(created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_activity_logs_endpoint ON activity_logs(endpoint)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_activity_logs_severity ON activity_logs(severity)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS token_adjustment_logs (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            admin_user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            adjustment_type VARCHAR(20) NOT NULL,
            amount INTEGER NOT NULL,
            balance_before INTEGER NOT NULL,
            balance_after INTEGER NOT NULL,
            reason TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_token_adjustment_logs_user_id ON token_adjustment_logs(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_token_adjustment_logs_created_at ON token_adjustment_logs(created_at)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_notifications (
            id VARCHAR(36) PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            message TEXT NOT NULL,
            severity VARCHAR(20) NOT NULL DEFAULT 'info',
            category VARCHAR(80) NOT NULL DEFAULT 'system',
            status VARCHAR(30) NOT NULL DEFAULT 'unread',
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            acknowledged_at TIMESTAMP,
            resolved_at TIMESTAMP
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_admin_notifications_status ON admin_notifications(status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_admin_notifications_created_at ON admin_notifications(created_at)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS feature_flags (
            id VARCHAR(36) PRIMARY KEY,
            key VARCHAR(120) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            rollout_percentage INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_feature_flags_key ON feature_flags(key)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS feature_flags")
    op.execute("DROP TABLE IF EXISTS admin_notifications")
    op.execute("DROP TABLE IF EXISTS token_adjustment_logs")
    op.execute("DROP TABLE IF EXISTS activity_logs")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS admin_notes")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS last_seen_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS last_login_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS manual_token_balance")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_super_admin")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_admin")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_suspended")
