"""Pydantic schemas for API request/response validation."""
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from typing import Optional, Any, List, Dict, Literal
from datetime import UTC, date, datetime
from enum import Enum

from app.config import normalize_connected_database_ssl_mode


# ==================== Authentication Schemas ====================

class TokenResponse(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    """User response schema."""
    id: str
    username: str
    email: str
    is_active: bool = True
    is_suspended: bool = False
    is_premium: bool
    is_admin: bool = False
    is_super_admin: bool = False
    admin_role: str = "user"
    plan_type: str
    daily_token_usage: int
    daily_token_limit: Optional[int]
    remaining_tokens: Optional[int]
    manual_token_balance: int = 0
    token_reset_date: date | None
    last_login_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    admin_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    server_time: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Config:
        from_attributes = True


class UserUpdateRequest(BaseModel):
    """Editable fields for the signed-in user."""

    username: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,48}[A-Za-z0-9])?$|^[A-Za-z0-9]$",
    )


class OAuthCodeExchangeRequest(BaseModel):
    """OAuth authorization request for browser-based code exchange.

    Either an authorization **code** or a pre‑obtained GitHub **access_token**
    should be provided.  The `state` value is only required when exchanging a
    code.  At least one of ``code`` or ``access_token`` must be present.
    """

    code: Optional[str] = None
    access_token: Optional[str] = None
    state: Optional[str] = None

    @model_validator(mode="after")
    def require_code_or_token(cls, values):
        # ``values`` is the model instance after validation
        if not values.code and not values.access_token:
            raise ValueError("either 'code' or 'access_token' must be supplied")
        return values


GithubAuthRequest = OAuthCodeExchangeRequest
GoogleAuthRequest = OAuthCodeExchangeRequest


# ==================== Database Connection Schemas ====================

class DatabaseConnectionCreate(BaseModel):
    """Create database connection."""
    name: str = Field(..., min_length=1, max_length=255)
    db_type: str = Field(..., pattern="^(postgresql|mysql)$")
    host: str = Field(..., min_length=1)
    port: int = Field(..., ge=1, le=65535)
    database_name: str = Field(..., min_length=1)
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    ssl_mode: Optional[str] = None

    @field_validator("ssl_mode", mode="before")
    @classmethod
    def normalize_ssl_mode(cls, value):
        return normalize_connected_database_ssl_mode(value)


class DatabaseConnectionUpdate(BaseModel):
    """Update database connection."""
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    database_name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    ssl_mode: Optional[str] = None

    @field_validator("ssl_mode", mode="before")
    @classmethod
    def normalize_ssl_mode(cls, value):
        return normalize_connected_database_ssl_mode(value)


class DatabaseConnectionResponse(BaseModel):
    """Database connection response (sensitive info redacted)."""
    id: str
    user_id: str
    name: str
    db_type: str
    host: str
    port: int
    database_name: str
    ssl_mode: Optional[str] = None
    is_active: bool
    last_synced_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SpreadsheetLinkCreate(BaseModel):
    """Create a spreadsheet source from a public or shareable link."""

    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=1, max_length=3000)

    @field_validator("url")
    @classmethod
    def validate_url_format(cls, value):
        value = value.strip()
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("Enter a valid http or https spreadsheet URL.")
        return value


class SourceRenameRequest(BaseModel):
    """Rename a connected source."""

    name: str = Field(..., min_length=1, max_length=255)


class SpreadsheetSourceResponse(BaseModel):
    """Spreadsheet source response."""

    id: str
    user_id: str
    name: str
    source_kind: str
    file_type: str
    original_filename: Optional[str] = None
    source_url: Optional[str] = None
    source_provider: Optional[str] = None
    status: str
    status_message: Optional[str] = None
    raw_schema_metadata: Dict[str, Any] = Field(default_factory=dict)
    dataset_payload: Dict[str, Any] = Field(default_factory=dict)
    analysis_metadata: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool
    last_synced_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ConnectedSourceResponse(BaseModel):
    """Unified source row for database and spreadsheet management."""

    id: str
    user_id: str
    name: str
    source_type: Literal["database", "spreadsheet"]
    source_kind: str
    status: str
    status_message: Optional[str] = None
    display_name: str
    detail: str
    last_synced_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConnectedSourceListResponse(BaseModel):
    """Unified connected source listing."""

    items: List[ConnectedSourceResponse]


class SyncSourceResponse(BaseModel):
    """Source sync response."""

    message: str
    source_id: str
    source_type: Literal["database", "spreadsheet"]
    schema_id: Optional[str] = None


# ==================== Schema Discovery Schemas ====================

class ColumnMetadataResponse(BaseModel):
    """Column metadata response."""
    id: str
    column_name: str
    nickname: Optional[str] = None
    data_type: str
    is_nullable: bool
    is_primary_key: bool
    is_foreign_key: bool
    foreign_key_reference: Optional[str]
    column_description: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class TableMetadataResponse(BaseModel):
    """Table metadata response."""
    id: str
    table_name: str
    table_description: Optional[str]
    row_count: Optional[int]
    columns: List[ColumnMetadataResponse]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SchemaRelationshipResponse(BaseModel):
    """Lightweight relationship details for ERD rendering."""
    id: str
    source_table_name: str
    source_column_name: str
    target_table_name: str
    target_column_name: str


class DatabaseSchemaResponse(BaseModel):
    """Database schema response."""
    id: str
    schema_name: str
    schema_description: Optional[str]
    tables: List[TableMetadataResponse]
    relationships: List[SchemaRelationshipResponse] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SchemaAnnotationUpsertRequest(BaseModel):
    """Create, update, or clear a schema annotation."""
    target_type: Literal["schema", "table", "column"]
    table_name: Optional[str] = None
    column_name: Optional[str] = None
    nickname: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def normalize_and_validate(self):
        def cleaned(value: Optional[str]) -> Optional[str]:
            if value is None:
                return None
            value = value.strip()
            return value or None

        self.table_name = cleaned(self.table_name)
        self.column_name = cleaned(self.column_name)
        self.nickname = cleaned(self.nickname)
        self.description = cleaned(self.description)

        if self.target_type == "schema":
            if self.table_name or self.column_name:
                raise ValueError("Schema annotations cannot target a table or column.")
            if self.nickname:
                raise ValueError("Nicknames are only supported for columns.")
        elif self.target_type == "table":
            if not self.table_name:
                raise ValueError("Table annotations require table_name.")
            if self.column_name:
                raise ValueError("Table annotations cannot target a column.")
            if self.nickname:
                raise ValueError("Nicknames are only supported for columns.")
        else:
            if not self.table_name or not self.column_name:
                raise ValueError("Column annotations require table_name and column_name.")

        return self


class SyncSchemaRequest(BaseModel):
    """Request to sync database schema."""
    connection_id: str


class SyncSchemaResponse(BaseModel):
    """Schema sync response."""
    message: str
    schema_id: str


# ==================== Chat and Query Schemas ====================

class ChatMessageCreate(BaseModel):
    """Create chat message (user prompt)."""
    session_id: str
    user_prompt: str = Field(..., min_length=1, max_length=5000)


class ChatMessageResponse(BaseModel):
    """Chat message response."""
    id: str
    session_id: str
    user_prompt: str
    generated_sql: Optional[str]
    query_result: Optional[Dict[str, Any]]
    ai_explanation: Optional[str]
    requires_confirmation: bool
    confirmation_token: Optional[str] = None
    execution_status: str
    created_at: datetime

    class Config:
        from_attributes = True


class ChatSessionCreate(BaseModel):
    """Create chat session."""
    connection_id: str
    title: Optional[str] = None


class ChatSessionResponse(BaseModel):
    """Chat session response."""
    id: str
    user_id: str
    connection_id: str
    connection_name: Optional[str] = None
    title: Optional[str]
    created_at: datetime
    updated_at: datetime
    messages: List[ChatMessageResponse]

    class Config:
        from_attributes = True


class ChatSessionSummaryResponse(BaseModel):
    """List-friendly chat session summary."""
    id: str
    user_id: str
    connection_id: str
    connection_name: Optional[str] = None
    title: Optional[str]
    created_at: datetime
    updated_at: datetime
    message_count: int
    last_user_prompt: Optional[str] = None
    last_response_preview: Optional[str] = None


class WorkspaceHistoryMessage(BaseModel):
    """Compact workspace conversation history item."""
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=5000)


class WorkspaceChatMessageCreate(BaseModel):
    """Create an ephemeral multi-source workspace message."""

    user_prompt: str = Field(..., min_length=1, max_length=5000)
    connection_ids: Optional[List[str]] = None
    active_connection_id: Optional[str] = None
    source_ids: Optional[List[str]] = None
    active_source_id: Optional[str] = None
    history: List[WorkspaceHistoryMessage] = []

    @model_validator(mode="after")
    def validate_workspace_targets(self):
        def clean_ids(values: Optional[List[str]]) -> List[str]:
            return list(
                dict.fromkeys(
                    str(source_id).strip()
                    for source_id in (values or [])
                    if str(source_id).strip()
                )
            )

        self.connection_ids = clean_ids(self.connection_ids)
        self.source_ids = clean_ids(self.source_ids)

        if not self.source_ids:
            self.source_ids = list(self.connection_ids)

        if not self.connection_ids:
            self.connection_ids = list(self.source_ids)

        if not self.source_ids:
            raise ValueError("Select at least one source.")

        if not self.active_source_id:
            self.active_source_id = self.active_connection_id

        if not self.active_connection_id:
            self.active_connection_id = self.active_source_id

        if not self.active_source_id:
            raise ValueError("Select an active source.")

        if self.active_source_id not in self.source_ids:
            raise ValueError("The active source must be one of the selected sources.")

        return self


class WorkspaceGeneratedQueryResponse(BaseModel):
    """Query or analysis generated for a specific workspace source."""

    source_id: Optional[str] = None
    source_type: Literal["database", "spreadsheet"] = "database"
    connection_id: str
    connection_name: str
    database_name: str
    sql: str
    status: str
    row_count: Optional[int] = None
    error: Optional[str] = None


class WorkspaceQueryResultResponse(BaseModel):
    """Executed workspace result for a specific source."""

    source_id: Optional[str] = None
    source_type: Literal["database", "spreadsheet"] = "database"
    connection_id: str
    connection_name: str
    database_name: str
    sql: str
    row_count: int
    truncated: bool = False
    rows: List[Dict[str, Any]] = []
    error: Optional[str] = None


class WorkspaceChatMessageResponse(BaseModel):
    """Workspace chat response spanning one or more sources."""

    id: str
    user_prompt: str
    ai_explanation: Optional[str]
    execution_status: str
    active_connection_id: str
    active_source_id: Optional[str] = None
    targeted_connection_ids: List[str] = []
    targeted_source_ids: List[str] = []
    generated_queries: List[WorkspaceGeneratedQueryResponse] = []
    query_results: List[WorkspaceQueryResultResponse] = []
    warnings: List[str] = []
    created_at: datetime


# ==================== Query Safety Schemas ====================

class QueryConfirmationRequest(BaseModel):
    """Request to confirm destructive query."""
    confirmation_token: str
    connection_id: str
    message_id: Optional[str] = None


class QueryConfirmationResponse(BaseModel):
    """Query confirmation response."""
    id: str
    query_text: str
    is_confirmed: bool
    is_rejected: bool
    expires_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class QueryValidationResponse(BaseModel):
    """Response from query validation."""
    is_safe: bool
    is_destructive: bool
    query_type: str  # SELECT, INSERT, UPDATE, DELETE, etc.
    warnings: List[str] = []
    error: Optional[str] = None


class ExecuteQueryRequest(BaseModel):
    """Request to execute a query."""
    connection_id: str
    query_text: str
    is_confirmed: bool = False  # For destructive queries


class ExecuteQueryResponse(BaseModel):
    """Query execution response."""
    success: bool
    rows_affected: Optional[int]
    result: Optional[Any]
    error: Optional[str]
    execution_time_ms: Optional[int]
    requires_confirmation: bool
    # For destructive queries needing confirmation
    confirmation_token: Optional[str]


# ==================== Query Log Schemas ====================

class QueryLogResponse(BaseModel):
    """Query log response."""
    id: str
    user_id: str
    connection_id: str
    query_text: str
    query_type: str
    is_destructive: bool
    execution_time_ms: Optional[int]
    row_count: Optional[int]
    error_message: Optional[str]
    status: str
    executed_at: datetime

    class Config:
        from_attributes = True


class QueryLogPageResponse(BaseModel):
    """Paginated query log response."""
    items: List[QueryLogResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class QueryStatsResponse(BaseModel):
    """Aggregated query stats for the current user."""
    total_queries: int
    successful_queries: int
    failed_queries: int
    success_rate: int


# ==================== Copilot Schemas ====================

class CopilotArtifactResponse(BaseModel):
    """Generic stored copilot artifact."""
    id: str
    user_id: str
    connection_id: Optional[str] = None
    source_id: Optional[str] = None
    source_type: Optional[str] = None
    session_id: Optional[str] = None
    artifact_type: str
    title: str
    prompt: Optional[str] = None
    summary: Optional[str] = None
    payload: Dict[str, Any]
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CopilotArtifactListResponse(BaseModel):
    """Grouped copilot artifacts for the workspace."""
    items: List[CopilotArtifactResponse]


class CopilotArtifactUpdate(BaseModel):
    """Update durable metadata for a stored copilot artifact."""
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    summary: Optional[str] = Field(default=None, max_length=10000)
    workspace: Optional[str] = Field(default=None, max_length=120)
    project: Optional[str] = Field(default=None, max_length=120)


class CopilotMemoryCreate(BaseModel):
    """Create a persistent copilot memory."""
    connection_id: Optional[str] = None
    title: str = Field(..., min_length=1, max_length=255)
    category: str = Field(default="general", min_length=1, max_length=50)
    content: str = Field(..., min_length=1, max_length=5000)


class CopilotMemoryResponse(BaseModel):
    """Persistent memory entry."""
    id: str
    user_id: str
    connection_id: Optional[str] = None
    title: str
    category: str
    content: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class InvestigationRequest(BaseModel):
    """Run a multi-step copilot investigation."""
    connection_id: str
    prompt: str = Field(..., min_length=1, max_length=5000)
    session_id: Optional[str] = None


class ExecutiveBriefingRequest(BaseModel):
    """Generate an executive briefing."""
    connection_id: str
    prompt: str = Field(
        default="Create an executive briefing for the latest business and product signals.",
        min_length=1,
        max_length=5000,
    )


class ScenarioRequest(BaseModel):
    """Generate a what-if scenario analysis."""
    connection_id: str
    prompt: str = Field(..., min_length=1, max_length=5000)
    session_id: Optional[str] = None


class RecommendationRequest(BaseModel):
    """Generate focused recommendations."""
    connection_id: str
    prompt: str = Field(..., min_length=1, max_length=5000)
    session_id: Optional[str] = None


class DashboardBuildRequest(BaseModel):
    """Build a saved dashboard from a natural-language prompt."""

    connection_id: Optional[str] = None
    source_id: Optional[str] = None
    source_ids: List[str] = []
    prompt: str = Field(
        default="Build an executive dashboard for this business.",
        min_length=1,
        max_length=5000,
    )

    @model_validator(mode="after")
    def validate_dashboard_source(self):
        self.source_ids = list(
            dict.fromkeys(
                source_id.strip()
                for source_id in self.source_ids
                if source_id and source_id.strip()
            )
        )
        if self.source_id and self.source_id.strip():
            self.source_id = self.source_id.strip()
        else:
            self.source_id = None
        if self.connection_id and self.connection_id.strip():
            self.connection_id = self.connection_id.strip()
        else:
            self.connection_id = None

        if not self.source_ids:
            if self.source_id:
                self.source_ids = [self.source_id]
            elif self.connection_id:
                self.source_ids = [self.connection_id]

        if not self.connection_id and self.source_id:
            self.connection_id = self.source_id
        if not self.source_id and self.connection_id:
            self.source_id = self.connection_id

        if not self.source_ids:
            raise ValueError("Select at least one source for the dashboard.")

        return self


class CopilotWatchlistCreate(BaseModel):
    """Create a new watchlist from a natural-language rule."""
    connection_id: str
    prompt: str = Field(..., min_length=1, max_length=5000)
    comparator: str = Field(default="gte", pattern="^(gt|gte|lt|lte)$")
    threshold_value: float


class CopilotWatchlistResponse(BaseModel):
    """Stored watchlist with latest evaluation."""
    id: str
    user_id: str
    connection_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    prompt: Optional[str] = None
    sql_text: str
    comparator: str
    threshold_value: float
    last_value: Optional[float] = None
    last_status: str
    last_summary: Optional[str] = None
    last_evaluated_at: Optional[datetime] = None
    payload: Dict[str, Any]
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CopilotOverviewResponse(BaseModel):
    """Copilot workspace overview."""
    recent_artifacts: List[CopilotArtifactResponse]
    memories: List[CopilotMemoryResponse]
    watchlists: List[CopilotWatchlistResponse]
    alerts: List[CopilotWatchlistResponse]


# ==================== Admin Schemas ====================

class AdminDashboardResponse(BaseModel):
    """Aggregated executive admin dashboard payload."""

    generated_at: datetime
    range: Dict[str, Any]
    overview: Dict[str, Any]
    growth: Dict[str, Any]
    active_users: Dict[str, Any]
    most_active_users: Dict[str, Any]
    ai_usage: Dict[str, Any]
    performance: Dict[str, Any]
    revenue: Dict[str, Any]
    system_health: Dict[str, Any]
    security: Dict[str, Any]
    retention: Dict[str, Any]
    engagement_trends: Dict[str, Any]
    recent: Dict[str, Any]
    notifications: List[Dict[str, Any]]
    feature_flags: List[Dict[str, Any]]


class AdminUserResponse(BaseModel):
    """User row for platform administration."""

    id: str
    username: str
    email: str
    is_active: bool
    is_suspended: bool = False
    is_premium: bool
    is_admin: bool = False
    is_super_admin: bool = False
    admin_role: str = "user"
    plan_type: str
    daily_token_usage: int
    daily_token_limit: Optional[int]
    remaining_tokens: Optional[int]
    manual_token_balance: int = 0
    token_reset_date: Optional[date] = None
    last_login_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    admin_notes: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    action_count: int = 0
    ai_request_count: int = 0
    tokens_used: int = 0

    class Config:
        from_attributes = True


class AdminUserPageResponse(BaseModel):
    """Paginated admin user listing."""

    items: List[AdminUserResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class AdminRoleUpdateRequest(BaseModel):
    """Update administrator privileges."""

    is_admin: bool
    is_super_admin: bool = False


class AdminUserStatusRequest(BaseModel):
    """Suspend or reactivate a user."""

    is_suspended: bool
    reason: Optional[str] = Field(default=None, max_length=1000)


class AdminUserNotesRequest(BaseModel):
    """Save internal admin notes on a user."""

    admin_notes: Optional[str] = Field(default=None, max_length=5000)


class TokenAdjustmentRequest(BaseModel):
    """Manual token adjustment request."""

    user_id: str
    adjustment_type: Literal["add", "deduct"]
    amount: int = Field(..., gt=0, le=10_000_000)
    reason: Optional[str] = Field(default=None, max_length=1000)


class TokenAdjustmentResponse(BaseModel):
    """Manual token adjustment audit entry."""

    id: str
    user_id: str
    admin_user_id: str
    adjustment_type: str
    amount: int
    balance_before: int
    balance_after: int
    reason: Optional[str] = None
    created_at: datetime
    user_email: Optional[str] = None
    admin_email: Optional[str] = None

    class Config:
        from_attributes = True


class TokenAdjustmentPageResponse(BaseModel):
    """Paginated token adjustment history."""

    items: List[TokenAdjustmentResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class ActivityLogResponse(BaseModel):
    """Queryable customer-support activity log entry."""

    id: str
    actor_user_id: Optional[str] = None
    actor_username: Optional[str] = None
    actor_email: Optional[str] = None
    action: str
    status: str
    severity: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    endpoint: Optional[str] = None
    method: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_payload: Dict[str, Any] = Field(default_factory=dict)
    response_status_code: Optional[int] = None
    response_time_ms: Optional[int] = None
    error_trace: Optional[str] = None
    session_id: Optional[str] = None
    geo_location: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    summary: str

    class Config:
        from_attributes = True


class ActivityLogPageResponse(BaseModel):
    """Paginated activity logs."""

    items: List[ActivityLogResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class AdminNotificationResponse(BaseModel):
    """Admin notification item."""

    id: str
    title: str
    message: str
    severity: str
    category: str
    status: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


class AdminNotificationStatusRequest(BaseModel):
    """Update notification read/resolution status."""

    status: Literal["unread", "acknowledged", "resolved"]


class FeatureFlagResponse(BaseModel):
    """Feature flag visible in admin settings."""

    id: str
    key: str
    name: str
    description: Optional[str] = None
    is_enabled: bool
    rollout_percentage: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class FeatureFlagUpdateRequest(BaseModel):
    """Update a feature flag."""

    is_enabled: bool
    rollout_percentage: int = Field(default=100, ge=0, le=100)


# ==================== Error Schemas ====================

class ErrorResponse(BaseModel):
    """Error response schema."""
    detail: str
    status_code: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ValidationErrorResponse(BaseModel):
    """Validation error response."""
    detail: str
    errors: List[Dict[str, Any]]
    status_code: int = 422
    timestamp: datetime = Field(default_factory=datetime.utcnow)
