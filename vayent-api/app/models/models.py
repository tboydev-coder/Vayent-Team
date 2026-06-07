"""
SQLAlchemy ORM models for Vayent application.
"""
from datetime import datetime
from sqlalchemy import (
    Column, Date, String, Integer, DateTime, Boolean, Text, Float,
    ForeignKey, JSON, Enum as SQLEnum, LargeBinary, UniqueConstraint
)
from sqlalchemy.orm import relationship
import enum
import uuid

from app.database import Base


class User(Base):
    """User model for Vayent application."""

    __tablename__ = "users"

    id = Column(String(36), primary_key=True,
                default=lambda: str(uuid.uuid4()))
    username = Column(String(255), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True, index=True)
    is_suspended = Column(Boolean, default=False, index=True, nullable=False)
    is_premium = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False, index=True, nullable=False)
    is_super_admin = Column(Boolean, default=False, index=True, nullable=False)
    plan_type = Column(String(20), default="free", nullable=False)
    monthly_token_usage = Column(Integer, default=0, nullable=False)
    reserved_token_usage = Column(Integer, default=0, nullable=False)
    manual_token_balance = Column(Integer, default=0, nullable=False)
    token_reset_date = Column(Date, default=lambda: datetime.utcnow().date(), nullable=False)
    last_login_at = Column(DateTime)
    last_seen_at = Column(DateTime, index=True)
    admin_notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Relationships
    oauth_accounts = relationship(
        "OAuthAccount", back_populates="user", cascade="all, delete-orphan")
    database_connections = relationship(
        "DatabaseConnection", back_populates="user", cascade="all, delete-orphan")
    chat_sessions = relationship(
        "ChatSession", back_populates="user", cascade="all, delete-orphan")
    query_logs = relationship(
        "QueryLog", back_populates="user", cascade="all, delete-orphan")
    query_confirmations = relationship(
        "QueryConfirmation", back_populates="user", cascade="all, delete-orphan")
    token_usage_logs = relationship(
        "TokenUsageLog", back_populates="user", cascade="all, delete-orphan")
    copilot_artifacts = relationship(
        "CopilotArtifact", back_populates="user", cascade="all, delete-orphan")
    copilot_memories = relationship(
        "CopilotMemory", back_populates="user", cascade="all, delete-orphan")
    copilot_watchlists = relationship(
        "CopilotWatchlist", back_populates="user", cascade="all, delete-orphan")
    activity_logs = relationship(
        "ActivityLog",
        back_populates="actor",
        cascade="all, delete-orphan",
        foreign_keys="ActivityLog.actor_user_id",
    )
    token_adjustments = relationship(
        "TokenAdjustmentLog",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="TokenAdjustmentLog.user_id",
    )
    spreadsheet_sources = relationship(
        "SpreadsheetSource", back_populates="user", cascade="all, delete-orphan")

    @property
    def effective_plan_type(self) -> str:
        """Return the normalized plan type for the user."""
        if self.plan_type in {"free", "paid"}:
            return self.plan_type
        return "paid" if self.is_premium else "free"

    @property
    def daily_token_usage(self):
        """Return consumed tokens for the current UTC day."""
        return self.monthly_token_usage or 0

    @property
    def daily_token_limit(self):
        """Return the configured daily token cap for the user."""
        from app.config import get_settings

        settings = get_settings()
        if self.effective_plan_type == "paid":
            base_limit = settings.paid_daily_token_limit or None
        else:
            base_limit = settings.free_daily_token_limit

        manual_balance = self.manual_token_balance or 0
        if base_limit is None:
            return None
        return base_limit + manual_balance

    @property
    def monthly_token_limit(self):
        """Backward-compatible alias for the active token cap."""
        return self.daily_token_limit

    @property
    def remaining_tokens(self):
        """Return remaining tokens for the current UTC day."""
        limit = self.daily_token_limit
        if limit is None:
            return None
        return max(limit - self.daily_token_usage, 0)

    @property
    def admin_role(self) -> str:
        """Return the user's administrative role label."""
        if self.is_super_admin:
            return "super_admin"
        if self.is_admin:
            return "admin"
        return "user"


class OAuthAccount(Base):
    """OAuth account linking (GitHub)."""

    __tablename__ = "oauth_accounts"

    id = Column(String(36), primary_key=True,
                default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey(
        "users.id"), nullable=False, index=True)
    provider = Column(String(50), nullable=False)  # "github"
    provider_user_id = Column(String(255), nullable=False)
    provider_username = Column(String(255))
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text)
    token_expires_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="oauth_accounts")

    __table_args__ = (
        # Unique constraint: one OAuth account per provider per user
    )


class DatabaseType(str, enum.Enum):
    """Supported database types."""
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"


class DatabaseConnection(Base):
    """User's connected database."""

    __tablename__ = "database_connections"

    id = Column(String(36), primary_key=True,
                default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey(
        "users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)  # User-friendly name
    db_type = Column(SQLEnum(DatabaseType), nullable=False)
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False)
    database_name = Column(String(255), nullable=False)
    ssl_mode = Column(String(20))
    # Credentials are stored encrypted (implementation in service layer)
    encrypted_username = Column(String(255), nullable=False)
    encrypted_password = Column(LargeBinary, nullable=False)

    is_active = Column(Boolean, default=True, index=True)
    last_synced_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="database_connections")
    schemas = relationship(
        "DatabaseSchema", back_populates="connection", cascade="all, delete-orphan")
    query_logs = relationship(
        "QueryLog", back_populates="connection", cascade="all, delete-orphan")
    copilot_artifacts = relationship(
        "CopilotArtifact", back_populates="connection", cascade="all, delete-orphan")
    copilot_memories = relationship(
        "CopilotMemory", back_populates="connection", cascade="all, delete-orphan")
    copilot_watchlists = relationship(
        "CopilotWatchlist", back_populates="connection", cascade="all, delete-orphan")
    schema_annotations = relationship(
        "SchemaAnnotation", back_populates="connection", cascade="all, delete-orphan")


class SpreadsheetSourceKind(str, enum.Enum):
    """Supported spreadsheet connection methods."""

    UPLOAD = "upload"
    LINK = "link"


class SpreadsheetSource(Base):
    """User-connected spreadsheet data source."""

    __tablename__ = "spreadsheet_sources"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    source_kind = Column(
        SQLEnum(SpreadsheetSourceKind, native_enum=False, length=20),
        nullable=False,
    )
    file_type = Column(String(20), nullable=False)
    original_filename = Column(String(255))
    source_url = Column(Text)
    source_provider = Column(String(80))
    status = Column(String(40), default="connected", nullable=False, index=True)
    status_message = Column(Text)
    raw_schema_metadata = Column(JSON, nullable=False, default=dict)
    dataset_payload = Column(JSON, nullable=False, default=dict)
    analysis_metadata = Column(JSON, nullable=False, default=dict)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    last_synced_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="spreadsheet_sources")


class DatabaseSchema(Base):
    """Schema metadata for connected database."""

    __tablename__ = "database_schemas"

    id = Column(String(36), primary_key=True,
                default=lambda: str(uuid.uuid4()))
    connection_id = Column(String(36), ForeignKey(
        "database_connections.id"), nullable=False, index=True)
    schema_name = Column(String(255), nullable=False)
    schema_description = Column(Text)
    raw_schema_metadata = Column(JSON)  # Full schema introspection data
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Relationships
    connection = relationship("DatabaseConnection", back_populates="schemas")
    tables = relationship(
        "TableMetadata", back_populates="schema", cascade="all, delete-orphan")


class TableMetadata(Base):
    """Table-level metadata."""

    __tablename__ = "table_metadata"

    id = Column(String(36), primary_key=True,
                default=lambda: str(uuid.uuid4()))
    schema_id = Column(String(36), ForeignKey(
        "database_schemas.id"), nullable=False, index=True)
    table_name = Column(String(255), nullable=False)
    table_description = Column(Text)
    row_count = Column(Integer)  # Approximate row count
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Relationships
    schema = relationship("DatabaseSchema", back_populates="tables")
    columns = relationship(
        "ColumnMetadata", back_populates="table", cascade="all, delete-orphan")


class ColumnMetadata(Base):
    """Column-level metadata."""

    __tablename__ = "column_metadata"

    id = Column(String(36), primary_key=True,
                default=lambda: str(uuid.uuid4()))
    table_id = Column(String(36), ForeignKey(
        "table_metadata.id"), nullable=False, index=True)
    column_name = Column(String(255), nullable=False)
    data_type = Column(String(100), nullable=False)
    is_nullable = Column(Boolean, default=True)
    is_primary_key = Column(Boolean, default=False)
    is_foreign_key = Column(Boolean, default=False)
    foreign_key_reference = Column(String(255))  # Format: "table.column"
    column_description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    table = relationship("TableMetadata", back_populates="columns")


class SchemaAnnotation(Base):
    """User-authored metadata that augments a synced schema."""

    __tablename__ = "schema_annotations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    connection_id = Column(
        String(36),
        ForeignKey("database_connections.id"),
        nullable=False,
        index=True,
    )
    target_type = Column(String(20), nullable=False)
    table_name = Column(String(255), nullable=False, default="")
    column_name = Column(String(255), nullable=False, default="")
    nickname = Column(String(255))
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    connection = relationship("DatabaseConnection", back_populates="schema_annotations")

    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "target_type",
            "table_name",
            "column_name",
            name="uq_schema_annotations_target",
        ),
    )


class ChatSession(Base):
    """Chat conversation session with a database."""

    __tablename__ = "chat_sessions"

    id = Column(String(36), primary_key=True,
                default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey(
        "users.id"), nullable=False, index=True)
    connection_id = Column(String(36), ForeignKey(
        "database_connections.id"), nullable=False, index=True)
    title = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="chat_sessions")
    messages = relationship(
        "ChatMessage", back_populates="session", cascade="all, delete-orphan")
    copilot_artifacts = relationship(
        "CopilotArtifact", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    """Individual message in a chat session."""

    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True,
                default=lambda: str(uuid.uuid4()))
    session_id = Column(String(36), ForeignKey(
        "chat_sessions.id"), nullable=False, index=True)
    user_prompt = Column(Text, nullable=False)
    generated_sql = Column(Text)  # The SQL generated from the prompt
    query_result = Column(JSON)  # Query execution result
    ai_explanation = Column(Text)  # Natural language explanation of results
    # Whether query is destructive
    requires_confirmation = Column(Boolean, default=False)
    # pending, executed, confirmed, rejected
    execution_status = Column(String(50), default="pending")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    session = relationship("ChatSession", back_populates="messages")


class QueryLog(Base):
    """Log of all executed queries."""

    __tablename__ = "query_logs"

    id = Column(String(36), primary_key=True,
                default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey(
        "users.id"), nullable=False, index=True)
    connection_id = Column(String(36), ForeignKey(
        "database_connections.id"), nullable=False, index=True)
    query_text = Column(Text, nullable=False)
    # SELECT, INSERT, UPDATE, DELETE, etc.
    query_type = Column(String(50), nullable=False)
    # INSERT, UPDATE, DELETE, etc.
    is_destructive = Column(Boolean, default=False)
    execution_time_ms = Column(Integer)  # Query execution time in milliseconds
    row_count = Column(Integer)  # Rows affected or returned
    error_message = Column(Text)  # If query failed
    status = Column(String(50), default="success")  # success, error, cancelled
    executed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="query_logs")
    connection = relationship("DatabaseConnection",
                              back_populates="query_logs")


class TokenUsageLog(Base):
    """Log of AI token usage for auditing and plan enforcement."""

    __tablename__ = "token_usage_logs"

    id = Column(String(36), primary_key=True,
                default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey(
        "users.id"), nullable=False, index=True)
    session_id = Column(String(36), ForeignKey(
        "chat_sessions.id"), nullable=True, index=True)
    message_id = Column(String(36), ForeignKey(
        "chat_messages.id"), nullable=True, index=True)
    request_kind = Column(String(50), default="chat", nullable=False)
    tokens_used = Column(Integer, nullable=False)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="token_usage_logs")


class QueryConfirmation(Base):
    """Confirmation records for destructive queries."""

    __tablename__ = "query_confirmations"

    id = Column(String(36), primary_key=True,
                default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey(
        "users.id"), nullable=False, index=True)
    connection_id = Column(String(36), ForeignKey(
        "database_connections.id"), nullable=True, index=True)
    query_text = Column(Text, nullable=False)
    # Token for confirmation link
    confirmation_token = Column(String(255), unique=True, index=True)
    is_confirmed = Column(Boolean, default=False)
    is_rejected = Column(Boolean, default=False)
    confirmed_at = Column(DateTime)
    rejected_at = Column(DateTime)
    # Confirmation expires after interval
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="query_confirmations")
    connection = relationship("DatabaseConnection")


class CopilotArtifact(Base):
    """Persisted copilot outputs such as investigations and dashboards."""

    __tablename__ = "copilot_artifacts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    connection_id = Column(
        String(36),
        ForeignKey("database_connections.id"),
        nullable=True,
        index=True,
    )
    source_id = Column(String(36), nullable=True, index=True)
    source_type = Column(String(30), nullable=True, index=True)
    session_id = Column(String(36), ForeignKey("chat_sessions.id"), nullable=True, index=True)
    artifact_type = Column(String(50), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    prompt = Column(Text)
    summary = Column(Text)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="copilot_artifacts")
    connection = relationship("DatabaseConnection", back_populates="copilot_artifacts")
    session = relationship("ChatSession", back_populates="copilot_artifacts")


class CopilotMemory(Base):
    """Persistent business or product memory for the copilot."""

    __tablename__ = "copilot_memories"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    connection_id = Column(
        String(36),
        ForeignKey("database_connections.id"),
        nullable=True,
        index=True,
    )
    title = Column(String(255), nullable=False)
    category = Column(String(50), default="general", nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="copilot_memories")
    connection = relationship("DatabaseConnection", back_populates="copilot_memories")


class CopilotWatchlist(Base):
    """Saved metric watchlists with alert evaluation state."""

    __tablename__ = "copilot_watchlists"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    connection_id = Column(
        String(36),
        ForeignKey("database_connections.id"),
        nullable=True,
        index=True,
    )
    title = Column(String(255), nullable=False)
    description = Column(Text)
    prompt = Column(Text)
    sql_text = Column(Text, nullable=False)
    comparator = Column(String(20), nullable=False, default="gte")
    threshold_value = Column(Float, nullable=False, default=0.0)
    last_value = Column(Float)
    last_status = Column(String(20), nullable=False, default="unknown")
    last_summary = Column(Text)
    last_evaluated_at = Column(DateTime)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="copilot_watchlists")
    connection = relationship("DatabaseConnection", back_populates="copilot_watchlists")


class ActivityLog(Base):
    """Queryable support and security activity log."""

    __tablename__ = "activity_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_user_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    actor_username = Column(String(255))
    actor_email = Column(String(255))
    action = Column(String(120), nullable=False, index=True)
    status = Column(String(40), default="success", nullable=False, index=True)
    severity = Column(String(20), default="info", nullable=False, index=True)
    resource_type = Column(String(80), index=True)
    resource_id = Column(String(255), index=True)
    endpoint = Column(String(255), index=True)
    method = Column(String(12))
    ip_address = Column(String(80))
    user_agent = Column(Text)
    request_payload = Column(JSON, nullable=False, default=dict)
    response_status_code = Column(Integer, index=True)
    response_time_ms = Column(Integer)
    error_trace = Column(Text)
    session_id = Column(String(255), index=True)
    geo_location = Column(String(255))
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    actor = relationship(
        "User",
        back_populates="activity_logs",
        foreign_keys=[actor_user_id],
    )


class TokenAdjustmentLog(Base):
    """Audit trail for manual admin token adjustments."""

    __tablename__ = "token_adjustment_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    admin_user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    adjustment_type = Column(String(20), nullable=False)
    amount = Column(Integer, nullable=False)
    balance_before = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=False)
    reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = relationship(
        "User",
        back_populates="token_adjustments",
        foreign_keys=[user_id],
    )
    admin_user = relationship("User", foreign_keys=[admin_user_id])


class AdminNotification(Base):
    """Admin-facing notification for operational alerts."""

    __tablename__ = "admin_notifications"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    severity = Column(String(20), default="info", nullable=False, index=True)
    category = Column(String(80), default="system", nullable=False, index=True)
    status = Column(String(30), default="unread", nullable=False, index=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    acknowledged_at = Column(DateTime)
    resolved_at = Column(DateTime)


class FeatureFlag(Base):
    """Feature flag metadata visible to admins."""

    __tablename__ = "feature_flags"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key = Column(String(120), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    is_enabled = Column(Boolean, default=False, nullable=False, index=True)
    rollout_percentage = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
