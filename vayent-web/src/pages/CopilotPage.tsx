import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "../services/api";
import { useAuthStore } from "../store/auth";
import {
  getActiveConnectionId,
  setActiveConnectionId,
} from "../utils/activeConnection";
import "../styles/copilot.css";
import type {
  Connection,
  CopilotArtifact,
  CopilotArtifactList,
  CopilotMemory,
  CopilotOverview,
  CopilotWatchlist,
} from "../types";

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const asRecordArray = (value: unknown): Record<string, unknown>[] =>
  Array.isArray(value) ? value.filter(isRecord) : [];

const asStringArray = (value: unknown): string[] =>
  Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];

const formatArtifactType = (value: string): string =>
  value.charAt(0).toUpperCase() + value.slice(1).replace(/_/g, " ");

const formatStatus = (value: string): string =>
  value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

const renderJson = (value: unknown): string =>
  JSON.stringify(value, null, 2) ?? "";

const artifactSectionDefinitions = [
  { key: "findings", label: "Findings" },
  { key: "recommendations", label: "Recommendations" },
  { key: "risks", label: "Risks" },
  { key: "opportunities", label: "Opportunities" },
  { key: "assumptions", label: "Assumptions" },
  { key: "upside", label: "Upside" },
  { key: "downside", label: "Downside" },
  { key: "watch_items", label: "Watch" },
] as const;

const metricMonitoringEnabled =
  import.meta.env.VITE_METRIC_MONITORING_ENABLED === "true";

const getArtifactSections = (payload: Record<string, unknown>) =>
  artifactSectionDefinitions
    .map(({ key, label }) => ({
      label,
      items: asStringArray(payload[key]),
    }))
    .filter((section) => section.items.length > 0);

const getArtifactPreviewItems = (payload: Record<string, unknown>) =>
  getArtifactSections(payload)
    .flatMap((section) => section.items)
    .slice(0, 3);

const getArtifactSummaryStats = (payload: Record<string, unknown>) => {
  const stats: string[] = [];
  const cards = asRecordArray(payload.cards);
  const evidenceQueries = asRecordArray(payload.evidence_queries);
  const sections = getArtifactSections(payload);

  if (cards.length > 0) {
    stats.push(
      `${cards.length} dashboard ${cards.length === 1 ? "card" : "cards"}`,
    );
  }

  if (evidenceQueries.length > 0) {
    stats.push(
      `${evidenceQueries.length} evidence ${evidenceQueries.length === 1 ? "query" : "queries"}`,
    );
  }

  sections.forEach((section) => {
    if (stats.length < 3) {
      stats.push(`${section.items.length} ${section.label.toLowerCase()}`);
    }
  });

  return stats.slice(0, 3);
};

const CopilotPage: React.FC = () => {
  const queryClient = useQueryClient();
  const currentUser = useAuthStore((state) => state.user);
  const [selectedConnectionId, setSelectedConnectionId] = useState("");
  const [investigationPrompt, setInvestigationPrompt] = useState(
    "Why is premium retention under pressure, and which segment should I fix first?",
  );
  const [briefingPrompt, setBriefingPrompt] = useState(
    "Create an executive briefing for the latest product, customer, and operating signals.",
  );
  const [recommendationPrompt, setRecommendationPrompt] = useState(
    "What should I do over the next 2 weeks to improve retention and revenue quality?",
  );
  const [scenarioPrompt, setScenarioPrompt] = useState(
    "What happens if we increase onboarding incentives for new customers next quarter?",
  );
  const [dashboardPrompt, setDashboardPrompt] = useState(
    "Build an executive dashboard for growth, retention, and operational health.",
  );
  const [watchlistPrompt, setWatchlistPrompt] = useState(
    "Alert me if churned customers this week reaches a concerning level.",
  );
  const [watchlistComparator, setWatchlistComparator] = useState<
    "gte" | "gt" | "lte" | "lt"
  >("gte");
  const [watchlistThreshold, setWatchlistThreshold] = useState("25");
  const [memoryTitle, setMemoryTitle] = useState("");
  const [memoryCategory, setMemoryCategory] = useState("goal");
  const [memoryContent, setMemoryContent] = useState("");
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [selectedArtifact, setSelectedArtifact] =
    useState<CopilotArtifact | null>(null);
  const [metricComingSoonOpen, setMetricComingSoonOpen] = useState(false);

  const { data: connections = [], isLoading: connectionsLoading } = useQuery<
    Connection[]
  >({
    queryKey: ["connections"],
    queryFn: async () => {
      const res = await api.get("/connections");
      return res.data as Connection[];
    },
  });

  useEffect(() => {
    if (
      selectedConnectionId &&
      connections.some((connection) => connection.id === selectedConnectionId)
    ) {
      return;
    }

    const storedConnectionId = getActiveConnectionId();
    const nextConnectionId =
      [storedConnectionId, connections[0]?.id].find(
        (connectionId): connectionId is string =>
          Boolean(
            connectionId &&
            connections.some((connection) => connection.id === connectionId),
          ),
      ) ?? "";

    if (nextConnectionId) {
      setSelectedConnectionId(nextConnectionId);
    }
  }, [connections, selectedConnectionId]);

  useEffect(() => {
    setSelectedArtifact(null);
  }, [selectedConnectionId]);

  useEffect(() => {
    if (selectedConnectionId) {
      setActiveConnectionId(selectedConnectionId);
    }
  }, [selectedConnectionId]);

  useEffect(() => {
    if (!selectedArtifact && !metricComingSoonOpen) {
      return undefined;
    }

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSelectedArtifact(null);
        setMetricComingSoonOpen(false);
      }
    };

    window.addEventListener("keydown", handleEscape);
    return () => {
      window.removeEventListener("keydown", handleEscape);
    };
  }, [metricComingSoonOpen, selectedArtifact]);

  const selectedConnection = useMemo(
    () =>
      connections.find(
        (connection) => connection.id === selectedConnectionId,
      ) ?? null,
    [connections, selectedConnectionId],
  );

  const invalidateCopilot = async () => {
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: ["copilot-overview", selectedConnectionId],
      }),
      queryClient.invalidateQueries({
        queryKey: ["copilot-artifacts", selectedConnectionId],
      }),
      queryClient.invalidateQueries({
        queryKey: ["copilot-watchlists", selectedConnectionId],
      }),
      queryClient.invalidateQueries({
        queryKey: ["copilot-memories", selectedConnectionId],
      }),
      queryClient.invalidateQueries({ queryKey: ["auth", "me"] }),
    ]);
  };

  const { data: overview } = useQuery<CopilotOverview>({
    queryKey: ["copilot-overview", selectedConnectionId],
    enabled: Boolean(selectedConnectionId),
    queryFn: async () => {
      const res = await api.get("/copilot/overview", {
        params: { connection_id: selectedConnectionId },
      });
      return res.data as CopilotOverview;
    },
  });

  const { data: artifactResponse } = useQuery<CopilotArtifactList>({
    queryKey: ["copilot-artifacts", selectedConnectionId],
    enabled: Boolean(selectedConnectionId),
    queryFn: async () => {
      const res = await api.get("/copilot/artifacts", {
        params: { connection_id: selectedConnectionId },
      });
      return res.data as CopilotArtifactList;
    },
  });

  const artifacts = artifactResponse?.items ?? [];
  const investigations = artifacts.filter(
    (item) => item.artifact_type === "investigation",
  );
  const briefings = artifacts.filter(
    (item) => item.artifact_type === "briefing",
  );
  const recommendations = artifacts.filter(
    (item) => item.artifact_type === "recommendation",
  );
  const scenarios = artifacts.filter(
    (item) => item.artifact_type === "scenario",
  );
  const dashboards = artifacts.filter(
    (item) => item.artifact_type === "dashboard",
  );
  const memories = overview?.memories ?? [];
  const watchlists = overview?.watchlists ?? [];
  const alerts = overview?.alerts ?? [];
  const tokenStatusLabel =
    currentUser?.remaining_tokens === null ||
    currentUser?.remaining_tokens === undefined
      ? `${currentUser?.daily_token_usage?.toLocaleString() ?? "0"} tokens used today`
      : `${currentUser.remaining_tokens.toLocaleString()} tokens left today`;

  const withMutationHandling = async <T,>(
    action: () => Promise<T>,
  ): Promise<T> => {
    setWorkspaceError(null);
    try {
      const result = await action();
      await invalidateCopilot();
      return result;
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "The copilot request failed.";
      setWorkspaceError(message);
      throw error;
    }
  };

  const investigationMutation = useMutation<CopilotArtifact, Error>({
    mutationFn: () =>
      withMutationHandling(async () => {
        const res = await api.post("/copilot/investigations", {
          connection_id: selectedConnectionId,
          prompt: investigationPrompt,
        });
        return res.data as CopilotArtifact;
      }),
  });

  const briefingMutation = useMutation<CopilotArtifact, Error>({
    mutationFn: () =>
      withMutationHandling(async () => {
        const res = await api.post("/copilot/briefings", {
          connection_id: selectedConnectionId,
          prompt: briefingPrompt,
        });
        return res.data as CopilotArtifact;
      }),
  });

  const recommendationMutation = useMutation<CopilotArtifact, Error>({
    mutationFn: () =>
      withMutationHandling(async () => {
        const res = await api.post("/copilot/recommendations", {
          connection_id: selectedConnectionId,
          prompt: recommendationPrompt,
        });
        return res.data as CopilotArtifact;
      }),
  });

  const scenarioMutation = useMutation<CopilotArtifact, Error>({
    mutationFn: () =>
      withMutationHandling(async () => {
        const res = await api.post("/copilot/scenarios", {
          connection_id: selectedConnectionId,
          prompt: scenarioPrompt,
        });
        return res.data as CopilotArtifact;
      }),
  });

  const dashboardMutation = useMutation<CopilotArtifact, Error>({
    mutationFn: () =>
      withMutationHandling(async () => {
        const res = await api.post("/copilot/dashboards", {
          connection_id: selectedConnectionId,
          prompt: dashboardPrompt,
        });
        return res.data as CopilotArtifact;
      }),
  });

  const memoryMutation = useMutation<CopilotMemory, Error>({
    mutationFn: () =>
      withMutationHandling(async () => {
        const res = await api.post("/copilot/memories", {
          connection_id: selectedConnectionId || null,
          title: memoryTitle,
          category: memoryCategory,
          content: memoryContent,
        });
        return res.data as CopilotMemory;
      }),
    onSuccess: () => {
      setMemoryTitle("");
      setMemoryContent("");
    },
  });

  const deleteMemoryMutation = useMutation<void, Error, string>({
    mutationFn: (memoryId) =>
      withMutationHandling(async () => {
        await api.delete(`/copilot/memories/${memoryId}`);
      }),
  });

  const watchlistMutation = useMutation<CopilotWatchlist, Error>({
    mutationFn: () =>
      withMutationHandling(async () => {
        const res = await api.post("/copilot/watchlists", {
          connection_id: selectedConnectionId,
          prompt: watchlistPrompt,
          comparator: watchlistComparator,
          threshold_value: Number(watchlistThreshold),
        });
        return res.data as CopilotWatchlist;
      }),
  });

  const evaluateWatchlistMutation = useMutation<
    CopilotWatchlist,
    Error,
    string
  >({
    mutationFn: (watchlistId) =>
      withMutationHandling(async () => {
        const res = await api.post(
          `/copilot/watchlists/${watchlistId}/evaluate`,
        );
        return res.data as CopilotWatchlist;
      }),
  });

  const showMetricComingSoon = () => {
    setMetricComingSoonOpen(true);
  };

  const renderStringList = (items: string[], label: string) =>
    items.length > 0 ? (
      <div className="copilot-list-block">
        <p className="page-kicker">{label}</p>
        <ul className="copilot-bullet-list">
          {items.map((item) => (
            <li key={`${label}-${item}`}>{item}</li>
          ))}
        </ul>
      </div>
    ) : null;

  const renderArtifactEvidence = (artifact: CopilotArtifact) => {
    const payload = artifact.payload ?? {};
    const evidenceQueries = asRecordArray(payload.evidence_queries);
    const cards = asRecordArray(payload.cards);
    const businessContextSnapshot = isRecord(payload.business_context_snapshot)
      ? payload.business_context_snapshot
      : null;
    const hasDetails =
      evidenceQueries.length > 0 || cards.length > 0 || businessContextSnapshot;

    if (!hasDetails) {
      return null;
    }

    return (
      <details className="copilot-evidence">
        <summary>Evidence panel</summary>

        {evidenceQueries.length > 0 ? (
          <div className="copilot-evidence-grid">
            {evidenceQueries.map((item, index) => (
              <div
                key={`${artifact.id}-evidence-${index}`}
                className="copilot-evidence-card"
              >
                <p className="page-kicker">
                  {String(item.question ?? item.label ?? "Evidence")}
                </p>
                <p>
                  {String(
                    item.rationale ?? item.status ?? "No rationale provided.",
                  )}
                </p>
                {"sql" in item ? (
                  <pre className="code-block">{String(item.sql)}</pre>
                ) : null}
                {"rows" in item ? (
                  <pre className="code-block">{renderJson(item.rows)}</pre>
                ) : null}
                {"error" in item ? (
                  <p className="copilot-error">{String(item.error)}</p>
                ) : null}
              </div>
            ))}
          </div>
        ) : null}

        {cards.length > 0 ? (
          <div className="copilot-dashboard-card-grid">
            {cards.map((card, index) => (
              <div
                key={`${artifact.id}-card-${index}`}
                className="metric-card copilot-dashboard-metric-card"
              >
                <p className="page-kicker">{String(card.title ?? "Card")}</p>
                <p className="copilot-metric-value">
                  {typeof card.value === "number"
                    ? card.value.toLocaleString()
                    : "--"}
                </p>
                <p>{String(card.description ?? "")}</p>

                {"sql" in card || "rows" in card || "error" in card ? (
                  <details className="copilot-dashboard-details">
                    <summary>Technical details</summary>
                    {"sql" in card ? (
                      <pre className="code-block">{String(card.sql)}</pre>
                    ) : null}
                    {"rows" in card ? (
                      <pre className="code-block">{renderJson(card.rows)}</pre>
                    ) : null}
                    {"error" in card ? (
                      <p className="copilot-error">{String(card.error)}</p>
                    ) : null}
                  </details>
                ) : null}
              </div>
            ))}
          </div>
        ) : null}

        {businessContextSnapshot ? (
          <pre className="code-block">
            {renderJson(businessContextSnapshot)}
          </pre>
        ) : null}
      </details>
    );
  };

  const renderArtifactPreview = (artifact: CopilotArtifact) => {
    const payload = artifact.payload ?? {};
    const previewItems = getArtifactPreviewItems(payload);
    const previewStats = getArtifactSummaryStats(payload);

    return (
      <button
        key={artifact.id}
        type="button"
        className="copilot-artifact-preview"
        onClick={() => setSelectedArtifact(artifact)}
      >
        <div className="copilot-artifact-preview-head">
          <div>
            <p className="page-kicker">
              {formatArtifactType(artifact.artifact_type)}
            </p>
            <h3>{artifact.title}</h3>
          </div>
          <span className="brand-pill">
            {new Date(artifact.created_at).toLocaleString()}
          </span>
        </div>

        <p className="copilot-artifact-preview-summary">
          {artifact.summary || artifact.prompt || "No summary available."}
        </p>

        {previewStats.length > 0 ? (
          <div className="copilot-artifact-preview-pills">
            {previewStats.map((item) => (
              <span
                key={`${artifact.id}-${item}`}
                className="brand-pill copilot-artifact-chip"
              >
                {item}
              </span>
            ))}
          </div>
        ) : null}

        {previewItems.length > 0 ? (
          <div className="copilot-artifact-preview-pills">
            {previewItems.map((item, index) => (
              <span
                key={`${artifact.id}-preview-${index}`}
                className="copilot-preview-pill"
              >
                {item}
              </span>
            ))}
          </div>
        ) : null}

        <span className="copilot-artifact-preview-action">Open details</span>
      </button>
    );
  };

  if (connectionsLoading) {
    return (
      <div className="app-page">
        <div className="app-empty">Loading copilot workspace...</div>
      </div>
    );
  }

  if (connections.length === 0) {
    return (
      <div className="app-page">
        <section className="dashboard-hero">
          <p className="page-kicker">Copilot Workspace</p>
          <h1 className="display-title">
            Connect a database before launching the advanced copilot.
          </h1>
          <p className="page-text">
            The investigation, dashboard, recommendation, and memory tools all
            need an active source to ground the workspace.
          </p>
          <Link to="/connections" className="brand-btn-primary">
            Add a connection
          </Link>
        </section>
      </div>
    );
  }

  return (
    <div className="app-page copilot-layout">
      <section className="dashboard-hero copilot-hero">
        <div className="copilot-hero-grid">
          <div className="copilot-hero-copy">
            <p className="page-kicker">Advanced Copilot</p>
            <h1 className="display-title">
              Run investigations, save intelligence, and make Vayent feel far
              more agentic.
            </h1>
            <p className="page-text">
              Investigation mode, executive briefings, business memory,
              recommendations, explainable evidence, scenarios, and auto-built
              dashboards now share one workspace.
            </p>
            <p className="copilot-token-note">
              Copilot actions now draw from the same token balance as chat,
              refresh your usage after every run, and stay trimmed for faster
              responses.
            </p>
          </div>

          <div className="app-panel-strong copilot-hero-panel">
            <label className="page-kicker" htmlFor="copilot-connection">
              Active Connection
            </label>
            <div className="glass-select-wrap">
              <select
                id="copilot-connection"
                className="input copilot-select glass-select"
                value={selectedConnectionId}
                onChange={(event) =>
                  setSelectedConnectionId(event.target.value)
                }
              >
                {connections.map((connection) => (
                  <option
                    key={connection.id}
                    value={connection.id}
                    className="input"
                  >
                    {connection.name} ({connection.db_type})
                  </option>
                ))}
              </select>
            </div>

            <div className="copilot-pill-row">
              <span className="brand-pill">{tokenStatusLabel}</span>
              <span className="brand-pill">
                {overview?.recent_artifacts.length ?? 0} saved artifacts
              </span>
              {metricMonitoringEnabled ? (
                <>
                  <span className="brand-pill">
                    {watchlists.length} watchlists
                  </span>
                  <span className="brand-pill">{alerts.length} active alerts</span>
                </>
              ) : (
                <span className="brand-pill">Monitoring coming soon</span>
              )}
            </div>

            <p className="copilot-connection-meta">
              {selectedConnection
                ? `${selectedConnection.database_name} on ${selectedConnection.host}:${selectedConnection.port}`
                : "Choose a source to ground the copilot."}
            </p>
          </div>
        </div>
      </section>

      {workspaceError ? (
        <div className="copilot-error-banner">{workspaceError}</div>
      ) : null}

      <section className="copilot-grid copilot-grid-two">
        <div className="app-panel-strong copilot-section">
          <div className="copilot-section-head">
            <div>
              <p className="page-kicker">Investigation Mode</p>
              <h2>Multi-step analysis</h2>
            </div>
            <button
              type="button"
              className="brand-btn-primary"
              onClick={() => investigationMutation.mutate()}
              disabled={
                !selectedConnectionId || investigationMutation.isPending
              }
            >
              {investigationMutation.isPending
                ? "Investigating..."
                : "Run investigation"}
            </button>
          </div>
          <textarea
            className="input copilot-textarea"
            value={investigationPrompt}
            onChange={(event) => setInvestigationPrompt(event.target.value)}
          />
          <div className="copilot-artifact-list">
            {investigations.map(renderArtifactPreview)}
          </div>
        </div>

        <div className="app-panel-strong copilot-section">
          <div className="copilot-section-head">
            <div>
              <p className="page-kicker">Scenario Mode</p>
              <h2>What-if planning</h2>
            </div>
            <button
              type="button"
              className="brand-btn-primary"
              onClick={() => scenarioMutation.mutate()}
              disabled={!selectedConnectionId || scenarioMutation.isPending}
            >
              {scenarioMutation.isPending ? "Modeling..." : "Run scenario"}
            </button>
          </div>
          <textarea
            className="input copilot-textarea"
            value={scenarioPrompt}
            onChange={(event) => setScenarioPrompt(event.target.value)}
          />
          <div className="copilot-artifact-list">
            {scenarios.map(renderArtifactPreview)}
          </div>
        </div>
      </section>

      <section className="copilot-grid copilot-grid-two">
        <div className="app-panel-strong copilot-section">
          <div className="copilot-section-head">
            <div>
              <p className="page-kicker">Executive Briefings</p>
              <h2>What changed and what matters</h2>
            </div>
            <button
              type="button"
              className="brand-btn-primary"
              onClick={() => briefingMutation.mutate()}
              disabled={!selectedConnectionId || briefingMutation.isPending}
            >
              {briefingMutation.isPending ? "Briefing..." : "Generate briefing"}
            </button>
          </div>
          <textarea
            className="input copilot-textarea"
            value={briefingPrompt}
            onChange={(event) => setBriefingPrompt(event.target.value)}
          />
          <div className="copilot-artifact-list">
            {briefings.map(renderArtifactPreview)}
          </div>
        </div>

        <div className="app-panel-strong copilot-section">
          <div className="copilot-section-head">
            <div>
              <p className="page-kicker">Recommendation Engine</p>
              <h2>Prioritized actions</h2>
            </div>
            <button
              type="button"
              className="brand-btn-primary"
              onClick={() => recommendationMutation.mutate()}
              disabled={
                !selectedConnectionId || recommendationMutation.isPending
              }
            >
              {recommendationMutation.isPending
                ? "Generating..."
                : "Generate actions"}
            </button>
          </div>
          <textarea
            className="input copilot-textarea"
            value={recommendationPrompt}
            onChange={(event) => setRecommendationPrompt(event.target.value)}
          />
          <div className="copilot-artifact-list">
            {recommendations.map(renderArtifactPreview)}
          </div>
        </div>
      </section>

      <section className="copilot-grid copilot-grid-two">
        <div className="app-panel-strong copilot-section">
          <div className="copilot-section-head">
            <div>
              <p className="page-kicker">Business Memory</p>
              <h2>Persistent context</h2>
            </div>
            <button
              type="button"
              className="brand-btn-primary"
              onClick={() => memoryMutation.mutate()}
              disabled={
                memoryMutation.isPending ||
                memoryTitle.trim().length === 0 ||
                memoryContent.trim().length === 0
              }
            >
              {memoryMutation.isPending ? "Saving..." : "Save memory"}
            </button>
          </div>
          <div className="copilot-form-grid">
            <input
              className="input"
              placeholder="Memory title"
              value={memoryTitle}
              onChange={(event) => setMemoryTitle(event.target.value)}
            />
            <input
              className="input"
              placeholder="Category"
              value={memoryCategory}
              onChange={(event) => setMemoryCategory(event.target.value)}
            />
          </div>
          <textarea
            className="input copilot-textarea"
            placeholder="Store goals, KPI definitions, priorities, ideal customer profile, current risks..."
            value={memoryContent}
            onChange={(event) => setMemoryContent(event.target.value)}
          />
          <div className="copilot-memory-list">
            {memories.map((memory) => (
              <article
                key={memory.id}
                className="metric-card copilot-memory-card"
              >
                <div className="copilot-memory-head">
                  <div>
                    <p className="page-kicker">{memory.category}</p>
                    <h3>{memory.title}</h3>
                  </div>
                  <button
                    type="button"
                    className="brand-btn-secondary"
                    onClick={() => deleteMemoryMutation.mutate(memory.id)}
                    disabled={deleteMemoryMutation.isPending}
                  >
                    Remove
                  </button>
                </div>
                <p>{memory.content}</p>
              </article>
            ))}
          </div>
        </div>

        <div className="app-panel-strong copilot-section">
          <div className="copilot-section-head">
            <div>
              <p className="page-kicker">Saved Watchlists and Alerts</p>
              <h2>Metric monitoring</h2>
            </div>
            <button
              type="button"
              className="brand-btn-primary"
              onClick={
                metricMonitoringEnabled
                  ? () => watchlistMutation.mutate()
                  : showMetricComingSoon
              }
              disabled={
                metricMonitoringEnabled &&
                (!selectedConnectionId ||
                  watchlistMutation.isPending ||
                  Number.isNaN(Number(watchlistThreshold)))
              }
            >
              {metricMonitoringEnabled
                ? watchlistMutation.isPending
                  ? "Saving..."
                  : "Create watchlist"
                : "Feature coming soon"}
            </button>
          </div>
          <textarea
            className="input copilot-textarea"
            value={watchlistPrompt}
            onChange={(event) => setWatchlistPrompt(event.target.value)}
            disabled={!metricMonitoringEnabled}
          />
          <div className="copilot-form-grid">
            <div className="glass-select-wrap">
              <select
                className="input glass-select"
                value={watchlistComparator}
                disabled={!metricMonitoringEnabled}
                onChange={(event) =>
                  setWatchlistComparator(
                    event.target.value as "gte" | "gt" | "lte" | "lt",
                  )
                }
              >
                <option value="gte">Alert when metric &gt;= threshold</option>
                <option value="gt">Alert when metric &gt; threshold</option>
                <option value="lte">Alert when metric &lt;= threshold</option>
                <option value="lt">Alert when metric &lt; threshold</option>
              </select>
            </div>
            <input
              className="input"
              type="number"
              value={watchlistThreshold}
              disabled={!metricMonitoringEnabled}
              onChange={(event) => setWatchlistThreshold(event.target.value)}
            />
          </div>

          {!metricMonitoringEnabled ? (
            <button
              type="button"
              className="copilot-coming-soon-panel"
              onClick={showMetricComingSoon}
            >
              <span className="brand-pill">Coming soon</span>
              <strong>Metric monitoring is not available yet.</strong>
              <span>
                Watchlists and automated alerts are disabled for this release.
                You can still use investigations, briefings, recommendations,
                scenarios, dashboards, and memories.
              </span>
            </button>
          ) : alerts.length > 0 ? (
            <div className="copilot-alert-strip">
              {alerts.map((alert) => (
                <div key={alert.id} className="copilot-alert-pill">
                  {alert.title}: {alert.last_summary}
                </div>
              ))}
            </div>
          ) : null}

          {metricMonitoringEnabled ? (
            <div className="copilot-watchlist-list">
              {watchlists.map((watchlist) => (
                <article
                  key={watchlist.id}
                  className="app-panel copilot-watchlist-card"
                >
                  <div className="copilot-watchlist-head">
                    <div>
                      <p className="page-kicker">
                        {formatStatus(watchlist.last_status)}
                      </p>
                      <h3>{watchlist.title}</h3>
                    </div>
                    <button
                      type="button"
                      className="brand-btn-secondary"
                      onClick={() =>
                        evaluateWatchlistMutation.mutate(watchlist.id)
                      }
                      disabled={evaluateWatchlistMutation.isPending}
                    >
                      Re-evaluate
                    </button>
                  </div>
                  <p>{watchlist.description || watchlist.prompt}</p>
                  <p className="copilot-watchlist-summary">
                    {watchlist.last_summary || "Not evaluated yet."}
                  </p>
                  <details className="copilot-evidence">
                    <summary>Show rule and evidence</summary>
                    <pre className="code-block">{watchlist.sql_text}</pre>
                    <pre className="code-block">
                      {renderJson(watchlist.payload)}
                    </pre>
                  </details>
                </article>
              ))}
            </div>
          ) : null}
        </div>
      </section>

      <section className="app-panel-strong copilot-section">
        <div className="copilot-section-head">
          <div>
            <p className="page-kicker">Auto-Dashboard Builder</p>
            <h2>Saved decision dashboards</h2>
          </div>
          <button
            type="button"
            className="brand-btn-primary"
            onClick={() => dashboardMutation.mutate()}
            disabled={!selectedConnectionId || dashboardMutation.isPending}
          >
            {dashboardMutation.isPending ? "Building..." : "Build dashboard"}
          </button>
        </div>
        <textarea
          className="input copilot-textarea"
          value={dashboardPrompt}
          onChange={(event) => setDashboardPrompt(event.target.value)}
        />
        <div className="copilot-artifact-list">
          {dashboards.map(renderArtifactPreview)}
        </div>
      </section>

      {metricComingSoonOpen ? (
        <div
          className="copilot-modal-overlay"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setMetricComingSoonOpen(false);
            }
          }}
        >
          <div
            className="app-panel-strong copilot-modal copilot-coming-soon-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="metric-coming-soon-title"
          >
            <div className="copilot-modal-head">
              <div>
                <p className="page-kicker">Feature coming soon</p>
                <h3 id="metric-coming-soon-title">Metric monitoring</h3>
              </div>

              <button
                type="button"
                className="brand-btn-secondary"
                onClick={() => setMetricComingSoonOpen(false)}
              >
                Close
              </button>
            </div>

            <p className="copilot-artifact-summary">
              Watchlists and automated metric alerts are disabled for this live
              release while the monitoring workflow is being finished. Use the
              other Copilot tools for read-only analysis and dashboard creation.
            </p>
          </div>
        </div>
      ) : null}

      {selectedArtifact ? (
        <div className="copilot-modal-overlay">
          <div
            className="app-panel-strong copilot-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="copilot-artifact-modal-title"
          >
            <div className="copilot-modal-head">
              <div>
                <p className="page-kicker">
                  {formatArtifactType(selectedArtifact.artifact_type)}
                </p>
                <h3 id="copilot-artifact-modal-title">
                  {selectedArtifact.title}
                </h3>
              </div>

              <button
                type="button"
                className="brand-btn-secondary"
                onClick={() => setSelectedArtifact(null)}
              >
                Close
              </button>
            </div>

            <div className="copilot-modal-body app-scroll-panel">
              <div className="copilot-pill-row">
                <span className="brand-pill">
                  {new Date(selectedArtifact.created_at).toLocaleString()}
                </span>
                {getArtifactSummaryStats(selectedArtifact.payload ?? {}).map(
                  (item) => (
                    <span
                      key={`${selectedArtifact.id}-modal-${item}`}
                      className="brand-pill copilot-artifact-chip"
                    >
                      {item}
                    </span>
                  ),
                )}
              </div>

              <p className="copilot-artifact-summary">
                {selectedArtifact.summary ||
                  selectedArtifact.prompt ||
                  "No summary available."}
              </p>

              {getArtifactSections(selectedArtifact.payload ?? {}).map(
                (section) => (
                  <React.Fragment
                    key={`${selectedArtifact.id}-${section.label}`}
                  >
                    {renderStringList(section.items, section.label)}
                  </React.Fragment>
                ),
              )}

              {renderArtifactEvidence(selectedArtifact)}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default CopilotPage;
