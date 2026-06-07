"""
Database connection and session management.
Uses SQLAlchemy with async support for production scalability.
"""
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from contextlib import asynccontextmanager
from fastapi import HTTPException, status
import logging
import re
import time
from typing import AsyncGenerator

from app.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    pass


# Global engine and session factory
_engine = None
_async_session_maker = None
_database_init_error = None
_last_database_init_attempt_at = 0.0
_DATABASE_RETRY_INTERVAL_SECONDS = 10.0


def _make_async_database_url(database_url: str) -> str:
    """Normalize common PostgreSQL URL schemes for SQLAlchemy asyncpg."""
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    if database_url.startswith("postgres+asyncpg://"):
        return "postgresql+asyncpg://" + database_url[len("postgres+asyncpg://"):]
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://"):]
    if database_url.startswith("postgres://"):
        return "postgresql+asyncpg://" + database_url[len("postgres://"):]
    return database_url


def _redact_database_url(database_url: str) -> str:
    """Render connection details without exposing the password."""
    try:
        return make_url(database_url).render_as_string(hide_password=True)
    except Exception:
        return re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", database_url)


def get_database_init_error() -> str | None:
    return _database_init_error


def database_is_initialized() -> bool:
    return _async_session_maker is not None and _database_init_error is None


def _database_unavailable_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Database is unavailable. Check the server DATABASE_URL setting.",
    )


def _ensure_connection_runtime_schema(sync_conn) -> None:
    """Apply additive compatibility columns for saved source connections."""
    sync_conn.execute(
        text(
            """
            ALTER TABLE IF EXISTS database_connections
            ADD COLUMN IF NOT EXISTS ssl_mode VARCHAR(20)
            """
        )
    )


def _ensure_runtime_schema(sync_conn) -> None:
    """Patch existing tables with required runtime columns."""
    statements = [
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS plan_type VARCHAR(20) NOT NULL DEFAULT 'free'
        """,
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS monthly_token_usage INTEGER NOT NULL DEFAULT 0
        """,
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS reserved_token_usage INTEGER NOT NULL DEFAULT 0
        """,
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS token_reset_date DATE NOT NULL DEFAULT CURRENT_DATE
        """,
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS is_suspended BOOLEAN NOT NULL DEFAULT FALSE
        """,
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE
        """,
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS is_super_admin BOOLEAN NOT NULL DEFAULT FALSE
        """,
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS manual_token_balance INTEGER NOT NULL DEFAULT 0
        """,
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP
        """,
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP
        """,
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS admin_notes TEXT
        """,
        """
        UPDATE users
        SET plan_type = CASE
            WHEN is_premium = TRUE THEN 'paid'
            ELSE 'free'
        END
        WHERE plan_type IS NULL OR plan_type = ''
        """,
        """
        UPDATE users
        SET token_reset_date = CURRENT_DATE
        WHERE token_reset_date IS NULL
        """,
        """
        ALTER TABLE database_connections
        ADD COLUMN IF NOT EXISTS ssl_mode VARCHAR(20)
        """,
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
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_spreadsheet_sources_user_id
        ON spreadsheet_sources(user_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_spreadsheet_sources_status
        ON spreadsheet_sources(status)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_spreadsheet_sources_is_active
        ON spreadsheet_sources(is_active)
        """,
        """
        CREATE TABLE IF NOT EXISTS copilot_artifacts (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            connection_id VARCHAR(36) REFERENCES database_connections(id) ON DELETE CASCADE,
            session_id VARCHAR(36) REFERENCES chat_sessions(id) ON DELETE CASCADE,
            artifact_type VARCHAR(50) NOT NULL,
            title VARCHAR(255) NOT NULL,
            prompt TEXT,
            summary TEXT,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_copilot_artifacts_user_id
        ON copilot_artifacts(user_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_copilot_artifacts_connection_id
        ON copilot_artifacts(connection_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_copilot_artifacts_session_id
        ON copilot_artifacts(session_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_copilot_artifacts_artifact_type
        ON copilot_artifacts(artifact_type)
        """,
        """
        ALTER TABLE copilot_artifacts
        ADD COLUMN IF NOT EXISTS source_id VARCHAR(36)
        """,
        """
        ALTER TABLE copilot_artifacts
        ADD COLUMN IF NOT EXISTS source_type VARCHAR(30)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_copilot_artifacts_source_id
        ON copilot_artifacts(source_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_copilot_artifacts_source_type
        ON copilot_artifacts(source_type)
        """,
        """
        CREATE TABLE IF NOT EXISTS copilot_memories (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            connection_id VARCHAR(36) REFERENCES database_connections(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            category VARCHAR(50) NOT NULL DEFAULT 'general',
            content TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_copilot_memories_user_id
        ON copilot_memories(user_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_copilot_memories_connection_id
        ON copilot_memories(connection_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS copilot_watchlists (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            connection_id VARCHAR(36) REFERENCES database_connections(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            description TEXT,
            prompt TEXT,
            sql_text TEXT NOT NULL,
            comparator VARCHAR(20) NOT NULL DEFAULT 'gte',
            threshold_value DOUBLE PRECISION NOT NULL DEFAULT 0,
            last_value DOUBLE PRECISION,
            last_status VARCHAR(20) NOT NULL DEFAULT 'unknown',
            last_summary TEXT,
            last_evaluated_at TIMESTAMP,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_copilot_watchlists_user_id
        ON copilot_watchlists(user_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_copilot_watchlists_connection_id
        ON copilot_watchlists(connection_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS schema_annotations (
            id VARCHAR(36) PRIMARY KEY,
            connection_id VARCHAR(36) NOT NULL REFERENCES database_connections(id) ON DELETE CASCADE,
            target_type VARCHAR(20) NOT NULL,
            table_name VARCHAR(255) NOT NULL DEFAULT '',
            column_name VARCHAR(255) NOT NULL DEFAULT '',
            nickname VARCHAR(255),
            description TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_schema_annotations_connection_id
        ON schema_annotations(connection_id)
        """,
        """
        ALTER TABLE query_confirmations
        ADD COLUMN IF NOT EXISTS connection_id VARCHAR(36)
        REFERENCES database_connections(id) ON DELETE CASCADE
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_query_confirmations_connection_id
        ON query_confirmations(connection_id)
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_schema_annotations_target
        ON schema_annotations(connection_id, target_type, table_name, column_name)
        """,
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
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_activity_logs_actor_user_id
        ON activity_logs(actor_user_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_activity_logs_created_at
        ON activity_logs(created_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_activity_logs_endpoint
        ON activity_logs(endpoint)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_activity_logs_severity
        ON activity_logs(severity)
        """,
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
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_token_adjustment_logs_user_id
        ON token_adjustment_logs(user_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_token_adjustment_logs_created_at
        ON token_adjustment_logs(created_at)
        """,
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
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_admin_notifications_status
        ON admin_notifications(status)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_admin_notifications_created_at
        ON admin_notifications(created_at)
        """,
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
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_feature_flags_key
        ON feature_flags(key)
        """,
    ]

    for statement in statements:
        sync_conn.execute(text(statement))


async def init_db():
    """Initialize database connection pool and tables."""
    global _engine, _async_session_maker, _database_init_error
    global _last_database_init_attempt_at

    settings = get_settings()

    db_url = _make_async_database_url(settings.database_url.strip())
    _database_init_error = None
    _last_database_init_attempt_at = time.monotonic()

    try:
        _engine = create_async_engine(
            db_url,
            echo=settings.debug and settings.log_sql_statements,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_recycle=settings.database_pool_recycle,
            pool_pre_ping=True,
        )

        _async_session_maker = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )

        async with _engine.begin() as conn:
            if settings.auto_create_tables:
                await conn.run_sync(Base.metadata.create_all)
                await conn.run_sync(_ensure_runtime_schema)
                logger.info("Database tables ensured via SQLAlchemy metadata")
            else:
                await conn.run_sync(_ensure_connection_runtime_schema)
                logger.info("AUTO_CREATE_TABLES disabled; expecting migrations to manage schema")

        logger.info("Database initialized successfully")
    except Exception as exc:
        _database_init_error = str(exc)
        logger.warning(
            "Database initialization failed for %s: %s",
            _redact_database_url(db_url),
            exc,
        )
        if _engine is not None:
            await _engine.dispose()
        _engine = None
        _async_session_maker = None
        raise


async def ensure_db_initialized(*, force: bool = False) -> bool:
    """Retry initialization after a startup failure so warm runtimes can recover."""
    global _last_database_init_attempt_at

    if database_is_initialized():
        return True

    now = time.monotonic()
    retry_age_seconds = now - _last_database_init_attempt_at
    if not force and retry_age_seconds < _DATABASE_RETRY_INTERVAL_SECONDS:
        return False

    try:
        await init_db()
        return True
    except Exception:
        return False


async def close_db():
    """Close database connections."""
    global _engine

    if _engine:
        await _engine.dispose()
        logger.info("Database connections closed")


async def check_db_health() -> bool:
    """Run a lightweight database health probe."""
    if not await ensure_db_initialized():
        return False

    try:
        async with _engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        return False


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session for dependency injection."""
    if not await ensure_db_initialized():
        raise _database_unavailable_exception()

    async with _async_session_maker() as session:
        try:
            yield session
        except Exception as e:
            await session.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context():
    """Get database session using context manager."""
    if not await ensure_db_initialized():
        raise _database_unavailable_exception()

    async with _async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Database error in context: {e}")
            raise
        finally:
            await session.close()
