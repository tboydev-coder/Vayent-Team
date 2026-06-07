export type DatabaseType = "postgresql" | "mysql";
export type ConnectionSslMode = "disable" | "prefer" | "require";

export interface User {
  id: string;
  username: string;
  email: string;
  is_active: boolean;
  is_suspended: boolean;
  is_premium: boolean;
  is_admin: boolean;
  is_super_admin: boolean;
  admin_role: "user" | "admin" | "super_admin" | string;
  plan_type: "free" | "paid";
  daily_token_usage: number;
  daily_token_limit: number | null;
  remaining_tokens: number | null;
  manual_token_balance: number;
  token_reset_date: string | null;
  last_login_at: string | null;
  last_seen_at: string | null;
  admin_notes: string | null;
  created_at: string;
  updated_at: string;
  server_time: string;
}

export interface Connection {
  id: string;
  user_id: string;
  name: string;
  db_type: DatabaseType;
  host: string;
  port: number;
  database_name: string;
  ssl_mode: ConnectionSslMode | string | null;
  is_active: boolean;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
}

export type SourceType = "database" | "spreadsheet";

export interface SpreadsheetSource {
  id: string;
  user_id: string;
  name: string;
  source_kind: "upload" | "link" | string;
  file_type: "xlsx" | "xls" | "csv" | string;
  original_filename: string | null;
  source_url: string | null;
  source_provider: string | null;
  status: string;
  status_message: string | null;
  raw_schema_metadata: Record<string, unknown>;
  dataset_payload: Record<string, unknown>;
  analysis_metadata: Record<string, unknown>;
  is_active: boolean;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface ConnectedSource {
  id: string;
  user_id: string;
  name: string;
  source_type: SourceType;
  source_kind: string;
  status: string;
  status_message: string | null;
  display_name: string;
  detail: string;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string | null;
  metadata: Record<string, unknown>;
}

export interface ConnectedSourceList {
  items: ConnectedSource[];
}

export interface ColumnMetadata {
  id: string;
  column_name: string;
  nickname: string | null;
  data_type: string;
  is_nullable: boolean;
  is_primary_key: boolean;
  is_foreign_key: boolean;
  foreign_key_reference: string | null;
  column_description: string | null;
  created_at: string;
}

export interface TableMetadata {
  id: string;
  table_name: string;
  table_description: string | null;
  row_count: number | null;
  columns: ColumnMetadata[];
  created_at: string;
  updated_at: string;
}

export interface DatabaseSchema {
  id: string;
  schema_name: string;
  schema_description: string | null;
  tables: TableMetadata[];
  relationships: SchemaRelationship[];
  created_at: string;
  updated_at: string;
}

export interface SchemaRelationship {
  id: string;
  source_table_name: string;
  source_column_name: string;
  target_table_name: string;
  target_column_name: string;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  user_prompt: string;
  generated_sql: string | null;
  query_result: Record<string, unknown> | null;
  ai_explanation: string | null;
  requires_confirmation: boolean;
  confirmation_token?: string | null;
  execution_status: string;
  created_at: string;
}

export interface ChatSession {
  id: string;
  user_id: string;
  connection_id: string;
  connection_name?: string | null;
  title: string | null;
  created_at: string;
  updated_at: string;
  messages: ChatMessage[];
}

export interface ChatSessionSummary {
  id: string;
  user_id: string;
  connection_id: string;
  connection_name?: string | null;
  title: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_user_prompt: string | null;
  last_response_preview: string | null;
}

export interface WorkspaceHistoryMessage {
  role: "user" | "assistant";
  content: string;
}

export interface WorkspaceGeneratedQuery {
  source_id?: string | null;
  source_type?: SourceType;
  connection_id: string;
  connection_name: string;
  database_name: string;
  sql: string;
  status: string;
  row_count: number | null;
  error: string | null;
}

export interface WorkspaceQueryResult {
  source_id?: string | null;
  source_type?: SourceType;
  connection_id: string;
  connection_name: string;
  database_name: string;
  sql: string;
  row_count: number;
  truncated: boolean;
  rows: Record<string, unknown>[];
  error: string | null;
}

export interface WorkspaceMessage {
  id: string;
  user_prompt: string;
  ai_explanation: string | null;
  execution_status: string;
  active_connection_id: string;
  active_source_id?: string | null;
  targeted_connection_ids: string[];
  targeted_source_ids?: string[];
  generated_queries: WorkspaceGeneratedQuery[];
  query_results: WorkspaceQueryResult[];
  warnings: string[];
  created_at: string;
}

export interface QueryLog {
  id: string;
  user_id: string;
  connection_id: string;
  query_text: string;
  query_type: string;
  is_destructive: boolean;
  execution_time_ms: number | null;
  row_count: number | null;
  error_message: string | null;
  status: string;
  executed_at: string;
}

export interface QueryLogPage {
  items: QueryLog[];
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
}

export interface QueryStats {
  total_queries: number;
  successful_queries: number;
  failed_queries: number;
  success_rate: number;
}

export interface ExecuteQueryResponse {
  success: boolean;
  rows_affected: number | null;
  result: unknown;
  error: string | null;
  execution_time_ms: number | null;
  requires_confirmation: boolean;
  confirmation_token: string | null;
}

export interface SyncSchemaResponse {
  message: string;
  schema_id: string;
}

export interface SyncSourceResponse {
  message: string;
  source_id: string;
  source_type: SourceType;
  schema_id: string | null;
}

export interface CopilotArtifact {
  id: string;
  user_id: string;
  connection_id: string | null;
  source_id: string | null;
  source_type: SourceType | string | null;
  session_id: string | null;
  artifact_type:
    | "investigation"
    | "briefing"
    | "recommendation"
    | "scenario"
    | "dashboard"
    | string;
  title: string;
  prompt: string | null;
  summary: string | null;
  payload: Record<string, unknown>;
  created_at: string;
  updated_at: string | null;
}

export interface CopilotArtifactList {
  items: CopilotArtifact[];
}

export interface CopilotMemory {
  id: string;
  user_id: string;
  connection_id: string | null;
  title: string;
  category: string;
  content: string;
  created_at: string;
  updated_at: string | null;
}

export interface CopilotWatchlist {
  id: string;
  user_id: string;
  connection_id: string | null;
  title: string;
  description: string | null;
  prompt: string | null;
  sql_text: string;
  comparator: "gt" | "gte" | "lt" | "lte" | string;
  threshold_value: number;
  last_value: number | null;
  last_status: "ok" | "alert" | "no_data" | "unknown" | string;
  last_summary: string | null;
  last_evaluated_at: string | null;
  payload: Record<string, unknown>;
  created_at: string;
  updated_at: string | null;
}

export interface CopilotOverview {
  recent_artifacts: CopilotArtifact[];
  memories: CopilotMemory[];
  watchlists: CopilotWatchlist[];
  alerts: CopilotWatchlist[];
}

export interface AdminGrowthMetric {
  current: number;
  previous: number;
  percent: number;
  direction: "increase" | "decrease" | string;
}

export interface AdminTopUser {
  id: string;
  username: string;
  email: string;
  value: number;
}

export interface AdminTrendPoint {
  date: string;
  value: number;
}

export interface AdminActivityLog {
  id: string;
  actor_user_id: string | null;
  actor_username: string | null;
  actor_email: string | null;
  action: string;
  status: string;
  severity: "info" | "warning" | "error" | "critical" | string;
  resource_type: string | null;
  resource_id: string | null;
  endpoint: string | null;
  method: string | null;
  ip_address: string | null;
  user_agent: string | null;
  request_payload: Record<string, unknown>;
  response_status_code: number | null;
  response_time_ms: number | null;
  error_trace: string | null;
  session_id: string | null;
  geo_location: string | null;
  details: Record<string, unknown>;
  created_at: string;
  summary: string;
}

export interface AdminActivityLogPage {
  items: AdminActivityLog[];
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
}

export interface AdminUser extends User {
  action_count: number;
  ai_request_count: number;
  tokens_used: number;
}

export interface AdminUserPage {
  items: AdminUser[];
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
}

export interface AdminTokenAdjustment {
  id: string;
  user_id: string;
  admin_user_id: string;
  adjustment_type: "add" | "deduct" | string;
  amount: number;
  balance_before: number;
  balance_after: number;
  reason: string | null;
  created_at: string;
  user_email: string | null;
  admin_email: string | null;
}

export interface AdminTokenAdjustmentPage {
  items: AdminTokenAdjustment[];
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
}

export interface AdminNotification {
  id: string;
  title: string;
  message: string;
  severity: "info" | "warning" | "error" | "critical" | string;
  category: string;
  status: "unread" | "acknowledged" | "resolved" | string;
  metadata: Record<string, unknown>;
  created_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
}

export interface AdminFeatureFlag {
  id: string;
  key: string;
  name: string;
  description: string | null;
  is_enabled: boolean;
  rollout_percentage: number;
  created_at: string;
  updated_at: string | null;
}

export interface AdminDashboard {
  generated_at: string;
  range: {
    label: string;
    start: string;
    end: string;
  };
  overview: {
    total_users: number;
    new_users: {
      today: number;
      this_week: number;
      this_month: number;
    };
    total_api_requests: number;
    total_ai_generations: number;
    total_query_logs: number;
    failed_login_attempts: number;
    admin_count: number;
    online_users: number;
    average_session_duration_seconds: number;
  };
  growth: {
    daily_signups: AdminGrowthMetric;
    weekly_signups: AdminGrowthMetric;
    monthly_signups: AdminGrowthMetric;
  };
  active_users: {
    dau: number;
    wau: number;
    mau: number;
    online_now: number;
  };
  most_active_users: {
    by_actions: AdminTopUser[];
    by_login_frequency: AdminTopUser[];
    by_token_usage: AdminTopUser[];
    by_ai_requests: AdminTopUser[];
  };
  ai_usage: {
    total_prompts: number;
    total_ai_requests: number;
    total_tokens: number;
    prompt_tokens: number;
    completion_tokens: number;
    average_prompt_tokens: number;
    average_prompt_length: number;
    most_used_models: Array<{ model: string; requests: number; tokens: number }>;
    token_consumption_per_user: AdminTopUser[];
    top_spending_users: AdminTopUser[];
    users_close_to_token_limits: Array<{
      id: string;
      username: string;
      email: string;
      remaining_tokens: number;
      daily_token_limit: number;
      usage_percent: number;
    }>;
  };
  performance: {
    api_response_time_ms: number;
    slowest_endpoints: Array<{
      endpoint: string;
      average_ms: number;
      max_ms: number;
      requests: number;
    }>;
    most_used_endpoints: Array<{ endpoint: string; requests: number }>;
    server_uptime_seconds: number;
    queue_jobs: {
      enabled: boolean;
      queued: number;
      running: number;
      failed: number;
      note: string;
    };
    database_query_performance: {
      average_query_ms: number;
      slowest_query_ms: number;
      logged_queries: number;
    };
    failed_requests: number;
    rate_limited_requests: number;
    error_rate: number;
  };
  revenue: {
    billing_detected: boolean;
    paid_users: number;
    free_users: number;
    estimated_mrr: number | null;
    note: string;
  };
  system_health: {
    status: "healthy" | "degraded" | "critical" | string;
    database: boolean;
    openai_configured: boolean;
    active_connections: number;
    server_uptime_seconds: number;
    error_rate: number;
  };
  security: {
    failed_login_attempts: number;
    unauthorized_admin_attempts: number;
    super_admin_denials: number;
    rate_limited_requests: number;
    suspended_users: number;
  };
  retention: {
    returning_users: number;
    new_users: number;
    retention_rate: number;
    inactive_users: number;
    churn_rate: number;
    drop_off_rate: number;
  };
  engagement_trends: {
    signups: AdminTrendPoint[];
    api_requests: AdminTrendPoint[];
    ai_requests: AdminTrendPoint[];
    token_usage: AdminTrendPoint[];
  };
  recent: {
    registered_users: AdminUser[];
    failed_actions: AdminActivityLog[];
    token_adjustments: AdminTokenAdjustment[];
  };
  notifications: AdminNotification[];
  feature_flags: AdminFeatureFlag[];
}
