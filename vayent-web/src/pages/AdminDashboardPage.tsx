import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";

import api, { API_BASE_URL } from "../services/api";
import { useAuthStore } from "../store/auth";
import type {
  AdminActivityLog,
  AdminActivityLogPage,
  AdminDashboard,
  AdminFeatureFlag,
  AdminNotification,
  AdminTokenAdjustment,
  AdminTokenAdjustmentPage,
  AdminTopUser,
  AdminUser,
  AdminUserPage,
  AdminTrendPoint,
} from "../types";
import "../styles/adminDashboard.css";

type AdminSection =
  | "overview"
  | "users"
  | "analytics"
  | "ai"
  | "tokens"
  | "logs"
  | "support"
  | "admins"
  | "health"
  | "notifications"
  | "security"
  | "settings";

const sections: Array<{ id: AdminSection; label: string }> = [
  { id: "overview", label: "Dashboard Overview" },
  { id: "users", label: "Users" },
  { id: "analytics", label: "Analytics" },
  { id: "ai", label: "AI Usage" },
  { id: "tokens", label: "Tokens" },
  { id: "logs", label: "Activity Logs" },
  { id: "support", label: "Customer Support" },
  { id: "admins", label: "Admin Management" },
  { id: "health", label: "System Health" },
  { id: "notifications", label: "Notifications" },
  { id: "security", label: "Security Logs" },
  { id: "settings", label: "Settings" },
];

const adminHeaders = { headers: { "X-Admin-CSRF": "1" } };

const formatNumber = (value: number | null | undefined): string =>
  typeof value === "number" ? value.toLocaleString() : "0";

const formatPercent = (value: number | null | undefined): string =>
  `${typeof value === "number" ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "0"}%`;

const formatDateTime = (value: string | null | undefined): string => {
  if (!value) return "Never";
  return new Date(value).toLocaleString();
};

const formatDuration = (seconds: number): string => {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
};

const safeJson = (value: unknown): string => {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

const sectionVariants = {
  hidden: { opacity: 0, y: 10 },
  visible: { opacity: 1, y: 0 },
};

type MotionDivProps = React.ComponentPropsWithoutRef<"div"> & {
  animate?: string;
  initial?: string;
  transition?: unknown;
  variants?: unknown;
};

const MotionDiv = motion.div as React.ComponentType<MotionDivProps>;

const KpiTile: React.FC<{
  label: string;
  value: string | number;
  note?: string;
  tone?: "neutral" | "good" | "warning" | "danger";
}> = ({ label, value, note, tone = "neutral" }) => (
  <MotionDiv
    className={`admin-kpi admin-kpi-${tone}`}
    variants={sectionVariants}
    initial="hidden"
    animate="visible"
    transition={{ duration: 0.25 }}
  >
    <p className="page-kicker">{label}</p>
    <p className="admin-kpi-value">{value}</p>
    {note ? <p className="admin-muted">{note}</p> : null}
  </MotionDiv>
);

const TrendBars: React.FC<{ points: AdminTrendPoint[]; label: string }> = ({
  points,
  label,
}) => {
  const maxValue = Math.max(...points.map((point) => point.value), 1);
  const trimmedPoints = points.slice(-14);

  return (
    <div className="admin-trend">
      <div className="admin-section-minihead">
        <p className="page-kicker">{label}</p>
        <span>{formatNumber(trimmedPoints.reduce((sum, point) => sum + point.value, 0))}</span>
      </div>
      <div className="admin-bars" aria-label={label}>
        {trimmedPoints.length === 0 ? (
          <div className="admin-empty-inline">No data</div>
        ) : (
          trimmedPoints.map((point) => (
            <div className="admin-bar-wrap" key={`${label}-${point.date}`}>
              <span
                className="admin-bar"
                style={{ height: `${Math.max((point.value / maxValue) * 100, 6)}%` }}
                title={`${point.date}: ${point.value}`}
              />
            </div>
          ))
        )}
      </div>
    </div>
  );
};

const TopUserList: React.FC<{ title: string; users: AdminTopUser[]; suffix?: string }> = ({
  title,
  users,
  suffix,
}) => (
  <div className="admin-list-panel">
    <div className="admin-section-minihead">
      <p className="page-kicker">{title}</p>
      <span>{users.length}</span>
    </div>
    <div className="admin-stack">
      {users.length === 0 ? (
        <p className="admin-empty-inline">No ranked users yet</p>
      ) : (
        users.map((item, index) => (
          <div className="admin-rank-row" key={`${title}-${item.id}`}>
            <div>
              <p>{index + 1}. {item.username}</p>
              <span>{item.email}</span>
            </div>
            <strong>
              {formatNumber(item.value)}
              {suffix ? ` ${suffix}` : ""}
            </strong>
          </div>
        ))
      )}
    </div>
  </div>
);

const EndpointTable: React.FC<{
  title: string;
  rows: Array<Record<string, string | number>>;
}> = ({ title, rows }) => (
  <div className="admin-list-panel">
    <p className="page-kicker">{title}</p>
    <div className="admin-table-wrap">
      <table className="admin-table">
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td>No data</td>
            </tr>
          ) : (
            rows.map((row, index) => (
              <tr key={`${title}-${index}`}>
                {Object.entries(row).map(([key, value]) => (
                  <td key={key}>{String(value)}</td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  </div>
);

const AdminDashboardPage: React.FC = () => {
  const queryClient = useQueryClient();
  const currentUser = useAuthStore((state) => state.user);
  const token = useAuthStore((state) => state.token);
  const [activeSection, setActiveSection] = useState<AdminSection>("overview");
  const [range, setRange] = useState<"day" | "week" | "month">("month");
  const [globalSearch, setGlobalSearch] = useState("");
  const [severityFilter, setSeverityFilter] = useState("");
  const [expandedLogId, setExpandedLogId] = useState<string | null>(null);
  const [selectedUserId, setSelectedUserId] = useState("");
  const [tokenAmount, setTokenAmount] = useState("1000");
  const [tokenAdjustmentType, setTokenAdjustmentType] = useState<"add" | "deduct">("add");
  const [tokenReason, setTokenReason] = useState("");
  const [notesUserId, setNotesUserId] = useState("");
  const [draftNotes, setDraftNotes] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [streamStatus, setStreamStatus] = useState<"connecting" | "live" | "polling">("connecting");

  const isSuperAdmin = Boolean(currentUser?.is_super_admin);

  const dashboardQuery = useQuery<AdminDashboard>({
    queryKey: ["admin-dashboard", range],
    refetchInterval: 30000,
    queryFn: async () => {
      const res = await api.get("/admin/dashboard", { params: { range } });
      return res.data as AdminDashboard;
    },
  });

  const usersQuery = useQuery<AdminUserPage>({
    queryKey: ["admin-users", globalSearch],
    queryFn: async () => {
      const res = await api.get("/admin/users", {
        params: { search: globalSearch || undefined, page_size: 50 },
      });
      return res.data as AdminUserPage;
    },
  });

  const logsQuery = useQuery<AdminActivityLogPage>({
    queryKey: ["admin-activity-logs", globalSearch, severityFilter],
    queryFn: async () => {
      const res = await api.get("/admin/activity-logs", {
        params: {
          search: globalSearch || undefined,
          severity: severityFilter || undefined,
          page_size: 50,
        },
      });
      return res.data as AdminActivityLogPage;
    },
  });

  const tokenHistoryQuery = useQuery<AdminTokenAdjustmentPage>({
    queryKey: ["admin-token-history", selectedUserId],
    queryFn: async () => {
      const res = await api.get("/admin/tokens/history", {
        params: {
          user_id: selectedUserId || undefined,
          page_size: 25,
        },
      });
      return res.data as AdminTokenAdjustmentPage;
    },
  });

  useEffect(() => {
    if (!token || range !== "month") {
      setStreamStatus("polling");
      return undefined;
    }

    const protocol = API_BASE_URL.startsWith("https") ? "wss" : "ws";
    const base = API_BASE_URL.replace(/^https?:\/\//, "");
    const websocket = new WebSocket(
      `${protocol}://${base}/admin/ws?access_token=${encodeURIComponent(token)}`,
    );

    websocket.onopen = () => setStreamStatus("live");
    websocket.onerror = () => setStreamStatus("polling");
    websocket.onclose = () => setStreamStatus("polling");
    websocket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data as string) as AdminDashboard;
        queryClient.setQueryData(["admin-dashboard", "month"], payload);
        setStreamStatus("live");
      } catch {
        setStreamStatus("polling");
      }
    };

    return () => {
      websocket.close();
    };
  }, [queryClient, range, token]);

  const dashboard = dashboardQuery.data;
  const users = useMemo(() => usersQuery.data?.items ?? [], [usersQuery.data?.items]);
  const logs = useMemo(() => logsQuery.data?.items ?? [], [logsQuery.data?.items]);
  const tokenHistory = useMemo(
    () => tokenHistoryQuery.data?.items ?? [],
    [tokenHistoryQuery.data?.items],
  );

  useEffect(() => {
    if (!selectedUserId && users.length > 0) {
      setSelectedUserId(users[0].id);
    }
  }, [selectedUserId, users]);

  const selectedUser = useMemo(
    () => users.find((user) => user.id === selectedUserId) ?? null,
    [selectedUserId, users],
  );

  const notesUser = useMemo(
    () => users.find((user) => user.id === notesUserId) ?? null,
    [notesUserId, users],
  );

  useEffect(() => {
    setDraftNotes(notesUser?.admin_notes ?? "");
  }, [notesUser?.admin_notes]);

  const invalidateAdminData = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["admin-dashboard"] }),
      queryClient.invalidateQueries({ queryKey: ["admin-users"] }),
      queryClient.invalidateQueries({ queryKey: ["admin-token-history"] }),
      queryClient.invalidateQueries({ queryKey: ["admin-activity-logs"] }),
    ]);
  };

  const roleMutation = useMutation<
    AdminUser,
    Error,
    { userId: string; isAdmin: boolean; isSuperAdmin: boolean }
  >({
    mutationFn: async (payload) => {
      const res = await api.patch(
        `/admin/users/${payload.userId}/role`,
        { is_admin: payload.isAdmin, is_super_admin: payload.isSuperAdmin },
        adminHeaders,
      );
      return res.data as AdminUser;
    },
    onSuccess: async () => {
      setNotice("Admin role updated.");
      await invalidateAdminData();
    },
    onError: (error) => setNotice(error.message),
  });

  const statusMutation = useMutation<
    AdminUser,
    Error,
    { userId: string; isSuspended: boolean }
  >({
    mutationFn: async (payload) => {
      const res = await api.patch(
        `/admin/users/${payload.userId}/status`,
        { is_suspended: payload.isSuspended, reason: "Admin dashboard action" },
        adminHeaders,
      );
      return res.data as AdminUser;
    },
    onSuccess: async () => {
      setNotice("User status updated.");
      await invalidateAdminData();
    },
    onError: (error) => setNotice(error.message),
  });

  const notesMutation = useMutation<AdminUser, Error, { userId: string; notes: string }>({
    mutationFn: async (payload) => {
      const res = await api.patch(
        `/admin/users/${payload.userId}/notes`,
        { admin_notes: payload.notes },
        adminHeaders,
      );
      return res.data as AdminUser;
    },
    onSuccess: async () => {
      setNotice("Admin notes saved.");
      await invalidateAdminData();
    },
    onError: (error) => setNotice(error.message),
  });

  const tokenMutation = useMutation<AdminTokenAdjustment, Error, void>({
    mutationFn: async () => {
      const res = await api.post(
        "/admin/tokens/adjust",
        {
          user_id: selectedUserId,
          adjustment_type: tokenAdjustmentType,
          amount: Number(tokenAmount),
          reason: tokenReason || null,
        },
        adminHeaders,
      );
      return res.data as AdminTokenAdjustment;
    },
    onSuccess: async () => {
      setNotice("Token adjustment logged.");
      setTokenReason("");
      await invalidateAdminData();
    },
    onError: (error) => setNotice(error.message),
  });

  const notificationMutation = useMutation<
    AdminNotification,
    Error,
    { id: string; status: "acknowledged" | "resolved" }
  >({
    mutationFn: async (payload) => {
      const res = await api.patch(
        `/admin/notifications/${payload.id}`,
        { status: payload.status },
        adminHeaders,
      );
      return res.data as AdminNotification;
    },
    onSuccess: async () => {
      setNotice("Notification updated.");
      await invalidateAdminData();
    },
    onError: (error) => setNotice(error.message),
  });

  const featureFlagMutation = useMutation<
    AdminFeatureFlag,
    Error,
    { flag: AdminFeatureFlag; enabled: boolean }
  >({
    mutationFn: async (payload) => {
      const res = await api.patch(
        `/admin/feature-flags/${payload.flag.key}`,
        {
          is_enabled: payload.enabled,
          rollout_percentage: payload.enabled ? 100 : 0,
        },
        adminHeaders,
      );
      return res.data as AdminFeatureFlag;
    },
    onSuccess: async () => {
      setNotice("Feature flag updated.");
      await invalidateAdminData();
    },
    onError: (error) => setNotice(error.message),
  });

  const exportLogs = async () => {
    const res = await api.get("/admin/activity-logs/export", {
      params: {
        search: globalSearch || undefined,
        severity: severityFilter || undefined,
      },
      responseType: "blob",
    });
    const blob = new Blob([res.data], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "vayent-activity-logs.csv";
    link.click();
    URL.revokeObjectURL(url);
  };

  const activeAdmins = users.filter((user) => user.is_admin || user.is_super_admin);

  if (dashboardQuery.isLoading && !dashboard) {
    return <div className="app-page admin-page">Loading admin dashboard...</div>;
  }

  if (dashboardQuery.isError) {
    return (
      <div className="app-page admin-page">
        <div className="app-panel-strong admin-denied">
          <p className="page-kicker">Admin</p>
          <h1>Dashboard unavailable</h1>
          <p>{dashboardQuery.error.message}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="app-page admin-page">
      <section className="admin-command">
        <div>
          <p className="page-kicker">Executive Admin</p>
          <h1>Platform Control</h1>
          <p className="admin-muted">
            {dashboard ? `Updated ${formatDateTime(dashboard.generated_at)}` : "Awaiting metrics"}
          </p>
        </div>

        <div className="admin-command-actions">
          <div className="admin-live">
            <span className={`admin-live-dot admin-live-${streamStatus}`} />
            {streamStatus === "live" ? "Live stream" : "Polling"}
          </div>

          <div className="admin-segmented">
            {(["day", "week", "month"] as const).map((item) => (
              <button
                key={item}
                type="button"
                className={range === item ? "admin-segment-active" : ""}
                onClick={() => setRange(item)}
              >
                {item}
              </button>
            ))}
          </div>

          <input
            className="input admin-search"
            value={globalSearch}
            onChange={(event) => setGlobalSearch(event.target.value)}
            placeholder="Search users, logs, endpoints"
          />

          <button type="button" className="brand-btn-secondary" onClick={() => window.print()}>
            Print PDF
          </button>
        </div>
      </section>

      {notice ? (
        <button type="button" className="admin-notice" onClick={() => setNotice(null)}>
          {notice}
        </button>
      ) : null}

      <div className="admin-workspace">
        <aside className="admin-nav">
          {sections.map((section) => (
            <button
              key={section.id}
              type="button"
              className={activeSection === section.id ? "admin-nav-active" : ""}
              onClick={() => setActiveSection(section.id)}
            >
              {section.label}
            </button>
          ))}
        </aside>

        <MotionDiv
          key={activeSection}
          className="admin-content"
          variants={sectionVariants}
          initial="hidden"
          animate="visible"
          transition={{ duration: 0.24 }}
        >
          {activeSection === "overview" && dashboard ? (
            <>
              <section className="admin-kpi-grid">
                <KpiTile label="Total Users" value={formatNumber(dashboard.overview.total_users)} />
                <KpiTile label="New Today" value={formatNumber(dashboard.overview.new_users.today)} note={`${formatPercent(dashboard.growth.daily_signups.percent)} daily`} />
                <KpiTile label="New Week" value={formatNumber(dashboard.overview.new_users.this_week)} note={`${formatPercent(dashboard.growth.weekly_signups.percent)} weekly`} />
                <KpiTile label="New Month" value={formatNumber(dashboard.overview.new_users.this_month)} note={`${formatPercent(dashboard.growth.monthly_signups.percent)} monthly`} />
                <KpiTile label="DAU" value={formatNumber(dashboard.active_users.dau)} />
                <KpiTile label="WAU" value={formatNumber(dashboard.active_users.wau)} />
                <KpiTile label="MAU" value={formatNumber(dashboard.active_users.mau)} />
                <KpiTile label="Online" value={formatNumber(dashboard.active_users.online_now)} tone="good" />
                <KpiTile label="API Requests" value={formatNumber(dashboard.overview.total_api_requests)} />
                <KpiTile label="AI Generations" value={formatNumber(dashboard.overview.total_ai_generations)} />
                <KpiTile label="Tokens" value={formatNumber(dashboard.ai_usage.total_tokens)} />
                <KpiTile label="Error Rate" value={formatPercent(dashboard.performance.error_rate)} tone={dashboard.performance.error_rate >= 10 ? "danger" : "neutral"} />
                <KpiTile label="Admins" value={formatNumber(dashboard.overview.admin_count)} />
                <KpiTile label="Session Avg" value={formatDuration(dashboard.overview.average_session_duration_seconds)} />
                <KpiTile label="Retention" value={formatPercent(dashboard.retention.retention_rate)} tone="good" />
              </section>

              <section className="admin-grid-two">
                <div className="app-panel-strong admin-panel">
                  <div className="admin-section-head">
                    <div>
                      <p className="page-kicker">Engagement Trends</p>
                      <h2>User movement</h2>
                    </div>
                    <span className="brand-pill">{dashboard.range.label}</span>
                  </div>
                  <div className="admin-chart-grid">
                    <TrendBars points={dashboard.engagement_trends.signups} label="Signups" />
                    <TrendBars points={dashboard.engagement_trends.api_requests} label="API Requests" />
                    <TrendBars points={dashboard.engagement_trends.ai_requests} label="AI Requests" />
                    <TrendBars points={dashboard.engagement_trends.token_usage} label="Token Usage" />
                  </div>
                </div>

                <div className="app-panel-strong admin-panel">
                  <div className="admin-section-head">
                    <div>
                      <p className="page-kicker">System Health</p>
                      <h2>{dashboard.system_health.status}</h2>
                    </div>
                    <span className={`admin-status admin-status-${dashboard.system_health.status}`}>
                      {dashboard.system_health.database ? "DB online" : "DB down"}
                    </span>
                  </div>
                  <div className="admin-health-grid">
                    <KpiTile label="Response Time" value={`${dashboard.performance.api_response_time_ms}ms`} />
                    <KpiTile label="Failed Requests" value={formatNumber(dashboard.performance.failed_requests)} tone={dashboard.performance.failed_requests ? "warning" : "good"} />
                    <KpiTile label="Rate Limited" value={formatNumber(dashboard.performance.rate_limited_requests)} />
                    <KpiTile label="Uptime" value={formatDuration(dashboard.performance.server_uptime_seconds)} />
                  </div>
                </div>
              </section>

              <section className="admin-grid-two">
                <TopUserList title="Actions" users={dashboard.most_active_users.by_actions} />
                <TopUserList title="Token Usage" users={dashboard.most_active_users.by_token_usage} suffix="tokens" />
              </section>
            </>
          ) : null}

          {activeSection === "users" ? (
            <section className="app-panel-strong admin-panel">
              <div className="admin-section-head">
                <div>
                  <p className="page-kicker">Users</p>
                  <h2>{formatNumber(usersQuery.data?.total_items)} accounts</h2>
                </div>
              </div>
              <UserTable
                users={users}
                isSuperAdmin={isSuperAdmin}
                onRole={(user, isAdmin, isSuperAdminNext) =>
                  roleMutation.mutate({
                    userId: user.id,
                    isAdmin,
                    isSuperAdmin: isSuperAdminNext,
                  })
                }
                onStatus={(user) =>
                  statusMutation.mutate({
                    userId: user.id,
                    isSuspended: !user.is_suspended,
                  })
                }
                onNotes={(user) => {
                  setNotesUserId(user.id);
                  setActiveSection("support");
                }}
              />
            </section>
          ) : null}

          {activeSection === "analytics" && dashboard ? (
            <section className="admin-grid-two">
              <div className="app-panel-strong admin-panel">
                <div className="admin-section-head">
                  <div>
                    <p className="page-kicker">User Analytics</p>
                    <h2>Retention and drop-off</h2>
                  </div>
                </div>
                <div className="admin-kpi-grid admin-kpi-grid-tight">
                  <KpiTile label="Returning" value={formatNumber(dashboard.retention.returning_users)} />
                  <KpiTile label="New" value={formatNumber(dashboard.retention.new_users)} />
                  <KpiTile label="Inactive" value={formatNumber(dashboard.retention.inactive_users)} tone="warning" />
                  <KpiTile label="Churn" value={formatPercent(dashboard.retention.churn_rate)} tone="warning" />
                </div>
              </div>
              <div className="app-panel-strong admin-panel">
                <TrendBars points={dashboard.engagement_trends.signups} label="Signup Trend" />
              </div>
            </section>
          ) : null}

          {activeSection === "ai" && dashboard ? (
            <section className="admin-grid-two">
              <div className="app-panel-strong admin-panel">
                <div className="admin-section-head">
                  <div>
                    <p className="page-kicker">AI Usage</p>
                    <h2>{formatNumber(dashboard.ai_usage.total_ai_requests)} requests</h2>
                  </div>
                </div>
                <div className="admin-kpi-grid admin-kpi-grid-tight">
                  <KpiTile label="Prompts" value={formatNumber(dashboard.ai_usage.total_prompts)} />
                  <KpiTile label="Avg Prompt Length" value={formatNumber(dashboard.ai_usage.average_prompt_length)} />
                  <KpiTile label="Prompt Tokens" value={formatNumber(dashboard.ai_usage.prompt_tokens)} />
                  <KpiTile label="Completion Tokens" value={formatNumber(dashboard.ai_usage.completion_tokens)} />
                </div>
              </div>
              <TopUserList title="Top Spending Users" users={dashboard.ai_usage.top_spending_users} suffix="tokens" />
              <TopUserList title="AI Requests" users={dashboard.most_active_users.by_ai_requests} />
              <div className="admin-list-panel">
                <p className="page-kicker">Token Limits</p>
                <div className="admin-stack">
                  {dashboard.ai_usage.users_close_to_token_limits.length === 0 ? (
                    <p className="admin-empty-inline">No users near token limits</p>
                  ) : (
                    dashboard.ai_usage.users_close_to_token_limits.map((user) => (
                      <div className="admin-rank-row" key={user.id}>
                        <div>
                          <p>{user.username}</p>
                          <span>{user.email}</span>
                        </div>
                        <strong>{formatPercent(user.usage_percent)}</strong>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </section>
          ) : null}

          {activeSection === "tokens" ? (
            <section className="admin-grid-two">
              <div className="app-panel-strong admin-panel">
                <div className="admin-section-head">
                  <div>
                    <p className="page-kicker">Token Management</p>
                    <h2>{selectedUser?.username || "Select user"}</h2>
                  </div>
                </div>
                <div className="admin-form-grid">
                  <label>
                    User
                    <div className="glass-select-wrap">
                      <select
                        className="input glass-select"
                        value={selectedUserId}
                        onChange={(event) => setSelectedUserId(event.target.value)}
                      >
                        {users.map((user) => (
                          <option key={user.id} value={user.id}>
                            {user.email}
                          </option>
                        ))}
                      </select>
                    </div>
                  </label>
                  <label>
                    Action
                    <div className="glass-select-wrap">
                      <select
                        className="input glass-select"
                        value={tokenAdjustmentType}
                        onChange={(event) =>
                          setTokenAdjustmentType(event.target.value as "add" | "deduct")
                        }
                      >
                        <option value="add">Add tokens</option>
                        <option value="deduct">Deduct tokens</option>
                      </select>
                    </div>
                  </label>
                  <label>
                    Amount
                    <input
                      className="input"
                      type="number"
                      min="1"
                      value={tokenAmount}
                      onChange={(event) => setTokenAmount(event.target.value)}
                    />
                  </label>
                  <label className="admin-form-wide">
                    Reason
                    <input
                      className="input"
                      value={tokenReason}
                      onChange={(event) => setTokenReason(event.target.value)}
                      placeholder="Support adjustment, goodwill credit, abuse reversal"
                    />
                  </label>
                </div>
                <button
                  type="button"
                  className="brand-btn-primary"
                  disabled={!selectedUserId || tokenMutation.isPending || Number(tokenAmount) <= 0}
                  onClick={() => tokenMutation.mutate()}
                >
                  {tokenMutation.isPending ? "Saving..." : "Apply Adjustment"}
                </button>
              </div>

              <TokenHistory adjustments={tokenHistory} />
            </section>
          ) : null}

          {activeSection === "logs" ? (
            <section className="app-panel-strong admin-panel">
              <div className="admin-section-head">
                <div>
                  <p className="page-kicker">Activity Logs</p>
                  <h2>{formatNumber(logsQuery.data?.total_items)} events</h2>
                </div>
                <div className="admin-inline-actions">
                  <div className="glass-select-wrap admin-filter">
                    <select
                      className="input glass-select"
                      value={severityFilter}
                      onChange={(event) => setSeverityFilter(event.target.value)}
                    >
                      <option value="">All severity</option>
                      <option value="info">Info</option>
                      <option value="warning">Warning</option>
                      <option value="error">Error</option>
                      <option value="critical">Critical</option>
                    </select>
                  </div>
                  <button type="button" className="brand-btn-secondary" onClick={() => void exportLogs()}>
                    Export CSV
                  </button>
                </div>
              </div>
              <ActivityLogList
                logs={logs}
                expandedLogId={expandedLogId}
                onToggle={(logId) => setExpandedLogId(expandedLogId === logId ? null : logId)}
              />
            </section>
          ) : null}

          {activeSection === "support" ? (
            <section className="admin-grid-two">
              <div className="app-panel-strong admin-panel">
                <div className="admin-section-head">
                  <div>
                    <p className="page-kicker">Customer Support</p>
                    <h2>{notesUser?.username || "Admin notes"}</h2>
                  </div>
                </div>
                <div className="admin-form-grid">
                  <label className="admin-form-wide">
                    User
                    <div className="glass-select-wrap">
                      <select
                        className="input glass-select"
                        value={notesUserId}
                        onChange={(event) => setNotesUserId(event.target.value)}
                      >
                        <option value="">Select a user</option>
                        {users.map((user) => (
                          <option key={user.id} value={user.id}>
                            {user.email}
                          </option>
                        ))}
                      </select>
                    </div>
                  </label>
                  <label className="admin-form-wide">
                    Notes
                    <textarea
                      className="input admin-textarea"
                      value={draftNotes}
                      onChange={(event) => setDraftNotes(event.target.value)}
                    />
                  </label>
                </div>
                <button
                  type="button"
                  className="brand-btn-primary"
                  disabled={!notesUserId || notesMutation.isPending}
                  onClick={() => notesMutation.mutate({ userId: notesUserId, notes: draftNotes })}
                >
                  {notesMutation.isPending ? "Saving..." : "Save Notes"}
                </button>
              </div>
              <div className="app-panel-strong admin-panel">
                <p className="page-kicker">Recently Failed</p>
                <ActivityLogList
                  logs={dashboard?.recent.failed_actions ?? []}
                  expandedLogId={expandedLogId}
                  onToggle={(logId) => setExpandedLogId(expandedLogId === logId ? null : logId)}
                  compact
                />
              </div>
            </section>
          ) : null}

          {activeSection === "admins" ? (
            <section className="app-panel-strong admin-panel">
              <div className="admin-section-head">
                <div>
                  <p className="page-kicker">Admin Management</p>
                  <h2>{activeAdmins.length} administrators</h2>
                </div>
              </div>
              <UserTable
                users={users}
                isSuperAdmin={isSuperAdmin}
                adminOnly
                onRole={(user, isAdmin, isSuperAdminNext) =>
                  roleMutation.mutate({
                    userId: user.id,
                    isAdmin,
                    isSuperAdmin: isSuperAdminNext,
                  })
                }
                onStatus={(user) =>
                  statusMutation.mutate({
                    userId: user.id,
                    isSuspended: !user.is_suspended,
                  })
                }
                onNotes={(user) => {
                  setNotesUserId(user.id);
                  setActiveSection("support");
                }}
              />
            </section>
          ) : null}

          {activeSection === "health" && dashboard ? (
            <section className="admin-grid-two">
              <EndpointTable
                title="Slowest Endpoints"
                rows={dashboard.performance.slowest_endpoints.map((endpoint) => ({
                  endpoint: endpoint.endpoint,
                  avg: `${endpoint.average_ms}ms`,
                  max: `${endpoint.max_ms}ms`,
                  requests: endpoint.requests,
                }))}
              />
              <EndpointTable
                title="Most Used Endpoints"
                rows={dashboard.performance.most_used_endpoints.map((endpoint) => ({
                  endpoint: endpoint.endpoint,
                  requests: endpoint.requests,
                }))}
              />
              <div className="app-panel-strong admin-panel">
                <p className="page-kicker">Database</p>
                <div className="admin-kpi-grid admin-kpi-grid-tight">
                  <KpiTile label="Avg Query" value={`${dashboard.performance.database_query_performance.average_query_ms}ms`} />
                  <KpiTile label="Slowest Query" value={`${dashboard.performance.database_query_performance.slowest_query_ms}ms`} />
                  <KpiTile label="Logged Queries" value={formatNumber(dashboard.performance.database_query_performance.logged_queries)} />
                  <KpiTile label="Connections" value={formatNumber(dashboard.system_health.active_connections)} />
                </div>
              </div>
              <div className="app-panel-strong admin-panel">
                <p className="page-kicker">Queue</p>
                <div className="admin-kpi-grid admin-kpi-grid-tight">
                  <KpiTile label="Queued" value={formatNumber(dashboard.performance.queue_jobs.queued)} />
                  <KpiTile label="Running" value={formatNumber(dashboard.performance.queue_jobs.running)} />
                  <KpiTile label="Failed" value={formatNumber(dashboard.performance.queue_jobs.failed)} />
                </div>
              </div>
            </section>
          ) : null}

          {activeSection === "notifications" && dashboard ? (
            <section className="app-panel-strong admin-panel">
              <div className="admin-section-head">
                <div>
                  <p className="page-kicker">Notifications</p>
                  <h2>{dashboard.notifications.length} alerts</h2>
                </div>
              </div>
              <div className="admin-notification-list">
                {dashboard.notifications.length === 0 ? (
                  <p className="admin-empty-inline">No active alerts</p>
                ) : (
                  dashboard.notifications.map((notification) => (
                    <div
                      key={notification.id}
                      className={`admin-notification admin-severity-${notification.severity}`}
                    >
                      <div>
                        <p>{notification.title}</p>
                        <span>{notification.message}</span>
                      </div>
                      {!notification.id.startsWith("dynamic") ? (
                        <div className="admin-inline-actions">
                          <button
                            type="button"
                            className="brand-btn-secondary"
                            onClick={() =>
                              notificationMutation.mutate({
                                id: notification.id,
                                status: "acknowledged",
                              })
                            }
                          >
                            Ack
                          </button>
                          <button
                            type="button"
                            className="brand-btn-secondary"
                            onClick={() =>
                              notificationMutation.mutate({
                                id: notification.id,
                                status: "resolved",
                              })
                            }
                          >
                            Resolve
                          </button>
                        </div>
                      ) : null}
                    </div>
                  ))
                )}
              </div>
            </section>
          ) : null}

          {activeSection === "security" && dashboard ? (
            <section className="admin-grid-two">
              <div className="app-panel-strong admin-panel">
                <p className="page-kicker">Security Logs</p>
                <div className="admin-kpi-grid admin-kpi-grid-tight">
                  <KpiTile label="Failed Logins" value={formatNumber(dashboard.security.failed_login_attempts)} tone="warning" />
                  <KpiTile label="Admin Denials" value={formatNumber(dashboard.security.unauthorized_admin_attempts)} tone="warning" />
                  <KpiTile label="Super Admin Denials" value={formatNumber(dashboard.security.super_admin_denials)} tone="warning" />
                  <KpiTile label="Suspended Users" value={formatNumber(dashboard.security.suspended_users)} />
                </div>
              </div>
              <div className="app-panel-strong admin-panel">
                <ActivityLogList
                  logs={logs.filter((log) => log.response_status_code === 401 || log.response_status_code === 403 || log.severity !== "info")}
                  expandedLogId={expandedLogId}
                  onToggle={(logId) => setExpandedLogId(expandedLogId === logId ? null : logId)}
                  compact
                />
              </div>
            </section>
          ) : null}

          {activeSection === "settings" && dashboard ? (
            <section className="app-panel-strong admin-panel">
              <div className="admin-section-head">
                <div>
                  <p className="page-kicker">Settings</p>
                  <h2>Feature flags</h2>
                </div>
              </div>
              <div className="admin-flag-list">
                {dashboard.feature_flags.map((flag) => (
                  <div className="admin-flag-row" key={flag.key}>
                    <div>
                      <p>{flag.name}</p>
                      <span>{flag.description}</span>
                    </div>
                    <button
                      type="button"
                      className={flag.is_enabled ? "brand-btn-primary" : "brand-btn-secondary"}
                      disabled={!isSuperAdmin || featureFlagMutation.isPending}
                      onClick={() =>
                        featureFlagMutation.mutate({
                          flag,
                          enabled: !flag.is_enabled,
                        })
                      }
                    >
                      {flag.is_enabled ? "On" : "Off"}
                    </button>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </MotionDiv>
      </div>
    </div>
  );
};

const UserTable: React.FC<{
  users: AdminUser[];
  isSuperAdmin: boolean;
  adminOnly?: boolean;
  onRole: (user: AdminUser, isAdmin: boolean, isSuperAdmin: boolean) => void;
  onStatus: (user: AdminUser) => void;
  onNotes: (user: AdminUser) => void;
}> = ({ users, isSuperAdmin, adminOnly = false, onRole, onStatus, onNotes }) => {
  const visibleUsers = adminOnly
    ? users.filter((user) => user.is_admin || user.is_super_admin)
    : users;

  return (
    <div className="admin-table-wrap">
      <table className="admin-table admin-user-table">
        <thead>
          <tr>
            <th>User</th>
            <th>Role</th>
            <th>Plan</th>
            <th>Usage</th>
            <th>Last Seen</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {visibleUsers.length === 0 ? (
            <tr>
              <td colSpan={6}>No users found</td>
            </tr>
          ) : (
            visibleUsers.map((user) => (
              <tr key={user.id}>
                <td>
                  <div className="admin-user-cell">
                    <span>{user.username.charAt(0).toUpperCase()}</span>
                    <div>
                      <p>{user.username}</p>
                      <small>{user.email}</small>
                    </div>
                  </div>
                </td>
                <td>
                  <span className="admin-status">
                    {user.is_super_admin ? "Super admin" : user.is_admin ? "Admin" : "User"}
                  </span>
                  {user.is_suspended ? <span className="admin-status admin-status-warning">Suspended</span> : null}
                </td>
                <td>{user.plan_type}</td>
                <td>
                  {formatNumber(user.daily_token_usage)}
                  {user.daily_token_limit ? ` / ${formatNumber(user.daily_token_limit)}` : " used"}
                </td>
                <td>{formatDateTime(user.last_seen_at)}</td>
                <td>
                  <div className="admin-row-actions">
                    <button
                      type="button"
                      className="brand-btn-secondary"
                      disabled={!isSuperAdmin}
                      onClick={() => onRole(user, !user.is_admin, false)}
                    >
                      {user.is_admin ? "Remove Admin" : "Make Admin"}
                    </button>
                    <button
                      type="button"
                      className="brand-btn-secondary"
                      disabled={!isSuperAdmin}
                      onClick={() => onRole(user, true, !user.is_super_admin)}
                    >
                      {user.is_super_admin ? "Remove Super" : "Make Super"}
                    </button>
                    <button type="button" className="brand-btn-secondary" onClick={() => onNotes(user)}>
                      Notes
                    </button>
                    <button type="button" className="brand-btn-danger" onClick={() => onStatus(user)}>
                      {user.is_suspended ? "Reactivate" : "Suspend"}
                    </button>
                  </div>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
};

const ActivityLogList: React.FC<{
  logs: AdminActivityLog[];
  expandedLogId: string | null;
  onToggle: (logId: string) => void;
  compact?: boolean;
}> = ({ logs, expandedLogId, onToggle, compact = false }) => (
  <div className={`admin-log-list ${compact ? "admin-log-list-compact" : ""}`}>
    {logs.length === 0 ? (
      <p className="admin-empty-inline">No activity logs</p>
    ) : (
      logs.map((log) => (
        <div className={`admin-log-row admin-severity-${log.severity}`} key={log.id}>
          <div className="admin-log-summary">
            <div>
              <p>{log.summary}</p>
              <span>
                {log.endpoint || log.resource_type || "system"} - {log.response_status_code ?? log.status}
              </span>
            </div>
            <button type="button" className="brand-btn-secondary" onClick={() => onToggle(log.id)}>
              {expandedLogId === log.id ? "Less" : "See More"}
            </button>
          </div>
          {expandedLogId === log.id ? (
            <div className="admin-log-details">
              <pre>{safeJson({
                user_id: log.actor_user_id,
                ip_address: log.ip_address,
                device_browser: log.user_agent,
                endpoint: log.endpoint,
                request_payload: log.request_payload,
                response_status_code: log.response_status_code,
                timestamp: log.created_at,
                error_trace: log.error_trace,
                session_id: log.session_id,
                geo_location: log.geo_location,
                details: log.details,
              })}</pre>
            </div>
          ) : null}
        </div>
      ))
    )}
  </div>
);

const TokenHistory: React.FC<{ adjustments: AdminTokenAdjustment[] }> = ({ adjustments }) => (
  <div className="app-panel-strong admin-panel">
    <div className="admin-section-head">
      <div>
        <p className="page-kicker">Token History</p>
        <h2>{adjustments.length} adjustments</h2>
      </div>
    </div>
    <div className="admin-stack">
      {adjustments.length === 0 ? (
        <p className="admin-empty-inline">No token adjustments yet</p>
      ) : (
        adjustments.map((adjustment) => (
          <div className="admin-rank-row" key={adjustment.id}>
            <div>
              <p>
                {adjustment.adjustment_type} {formatNumber(adjustment.amount)} tokens
              </p>
              <span>
                {adjustment.user_email || adjustment.user_id} - {formatDateTime(adjustment.created_at)}
              </span>
            </div>
            <strong>{formatNumber(adjustment.balance_after)}</strong>
          </div>
        ))
      )}
    </div>
  </div>
);

export default AdminDashboardPage;
