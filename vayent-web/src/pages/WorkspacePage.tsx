import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "../services/api";
import {
  getActiveConnectionId,
  setActiveConnectionId,
} from "../utils/activeConnection";
import "../styles/chatPage.css";
import "../styles/workspace.css";
import type {
  ConnectedSource,
  ConnectedSourceList,
  WorkspaceHistoryMessage,
  WorkspaceMessage,
} from "../types";

const WORKSPACE_SOURCES_STORAGE_KEY = "vayent_workspace_source_ids";
const LEGACY_WORKSPACE_CONNECTIONS_STORAGE_KEY = "vayent_workspace_connection_ids";

const readStoredWorkspaceSources = (): string[] => {
  if (typeof window === "undefined") {
    return [];
  }

  try {
    const raw =
      window.localStorage.getItem(WORKSPACE_SOURCES_STORAGE_KEY) ??
      window.localStorage.getItem(LEGACY_WORKSPACE_CONNECTIONS_STORAGE_KEY);
    if (!raw) {
      return [];
    }

    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((value): value is string => typeof value === "string")
      : [];
  } catch {
    return [];
  }
};

const buildWorkspaceHistory = (
  messages: WorkspaceMessage[],
): WorkspaceHistoryMessage[] => {
  const history: WorkspaceHistoryMessage[] = [];

  messages.slice(-6).forEach((message) => {
    history.push({
      role: "user",
      content: message.user_prompt,
    });

    const assistantParts = [];
    if (message.ai_explanation) {
      assistantParts.push(message.ai_explanation);
    }
    if (message.generated_queries.length > 0) {
      assistantParts.push(
        `Analyzed: ${message.generated_queries
          .map((query) => query.connection_name)
          .join(", ")}`,
      );
    }
    if (message.query_results.length > 0) {
      assistantParts.push(
        message.query_results
          .map((result) =>
            result.error
              ? `${result.connection_name} failed: ${result.error}`
              : `${result.connection_name}: ${result.row_count} rows`,
          )
          .join(" | "),
      );
    }

    if (assistantParts.length > 0) {
      history.push({
        role: "assistant",
        content: assistantParts.join(" "),
      });
    }
  });

  return history;
};

const sourceKindLabel = (source: ConnectedSource) =>
  source.source_type === "database"
    ? source.source_kind
    : source.source_kind === "link"
      ? "Excel link"
      : "Spreadsheet";

const WorkspacePage: React.FC = () => {
  const queryClient = useQueryClient();
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [activeSourceId, setActiveSourceId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState<WorkspaceMessage[]>([]);
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);
  const [transientError, setTransientError] = useState<string | null>(null);

  const { data: sourceList, isLoading } = useQuery<ConnectedSourceList>({
    queryKey: ["connected-sources"],
    queryFn: async () => {
      const res = await api.get("/connections/sources");
      return res.data as ConnectedSourceList;
    },
  });

  const sources = useMemo(() => sourceList?.items ?? [], [sourceList?.items]);
  const sourceLookup = useMemo(
    () => new Map(sources.map((source) => [source.id, source])),
    [sources],
  );

  const selectedSources = useMemo(
    () =>
      selectedSourceIds
        .map((sourceId) => sourceLookup.get(sourceId))
        .filter((source): source is ConnectedSource => Boolean(source)),
    [sourceLookup, selectedSourceIds],
  );

  const selectedDatabaseSources = selectedSources.filter(
    (source) => source.source_type === "database",
  );
  const selectedSpreadsheetSources = selectedSources.filter(
    (source) => source.source_type === "spreadsheet",
  );
  const activeSource =
    selectedSources.find((source) => source.id === activeSourceId) ?? null;
  const activeDatabaseSource =
    activeSource?.source_type === "database"
      ? activeSource
      : selectedDatabaseSources[0] ?? null;

  useEffect(() => {
    if (sources.length === 0) {
      setSelectedSourceIds([]);
      setActiveSourceId("");
      return;
    }

    const validIds = new Set(sources.map((source) => source.id));
    const storedSelected = readStoredWorkspaceSources().filter((id) =>
      validIds.has(id),
    );
    const storedActive = getActiveConnectionId();
    const fallbackActive =
      (storedActive && validIds.has(storedActive) ? storedActive : null) ??
      sources[0]?.id ??
      "";

    setSelectedSourceIds((current) => {
      if (current.length > 0) {
        return current.filter((id) => validIds.has(id));
      }

      if (storedSelected.length > 0) {
        return storedSelected;
      }

      return fallbackActive ? [fallbackActive] : [];
    });

    setActiveSourceId((current) => {
      if (current && validIds.has(current)) {
        return current;
      }

      if (storedActive && validIds.has(storedActive)) {
        return storedActive;
      }

      return storedSelected[0] ?? fallbackActive;
    });
  }, [sources]);

  useEffect(() => {
    if (selectedSourceIds.length === 0) {
      setActiveSourceId("");
      return;
    }

    if (!selectedSourceIds.includes(activeSourceId)) {
      setActiveSourceId(selectedSourceIds[0]);
    }
  }, [activeSourceId, selectedSourceIds]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    window.localStorage.setItem(
      WORKSPACE_SOURCES_STORAGE_KEY,
      JSON.stringify(selectedSourceIds),
    );
  }, [selectedSourceIds]);

  useEffect(() => {
    setActiveConnectionId(activeDatabaseSource?.id ?? null);
  }, [activeDatabaseSource?.id]);

  const sendMessageMutation = useMutation<WorkspaceMessage, Error, string>({
    mutationFn: async (userPrompt) => {
      const res = await api.post("/chat/workspace/message", {
        user_prompt: userPrompt,
        source_ids: selectedSourceIds,
        active_source_id: activeSourceId,
        connection_ids: selectedDatabaseSources.map((source) => source.id),
        active_connection_id: activeDatabaseSource?.id ?? activeSourceId,
        history: buildWorkspaceHistory(messages),
      });
      return res.data as WorkspaceMessage;
    },
    onSuccess: async (message) => {
      setPendingPrompt(null);
      setTransientError(null);
      setMessages((current) => [...current, message]);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["logs"] }),
        queryClient.invalidateQueries({ queryKey: ["auth", "me"] }),
      ]);
    },
    onError: (error) => {
      setPendingPrompt(null);
      setTransientError(error.message);
    },
  });

  const toggleSource = (sourceId: string) => {
    setSelectedSourceIds((current) => {
      if (current.includes(sourceId)) {
        return current.filter((id) => id !== sourceId);
      }

      return [...current, sourceId];
    });
  };

  const applySourcePreset = (
    preset: "all" | "database" | "spreadsheet" | "mixed",
  ) => {
    const databaseIds = sources
      .filter((source) => source.source_type === "database")
      .map((source) => source.id);
    const spreadsheetIds = sources
      .filter((source) => source.source_type === "spreadsheet")
      .map((source) => source.id);

    const nextIds =
      preset === "database"
        ? databaseIds
        : preset === "spreadsheet"
          ? spreadsheetIds
          : preset === "mixed"
            ? [...databaseIds.slice(0, 1), ...spreadsheetIds.slice(0, 1)]
            : sources.map((source) => source.id);

    setSelectedSourceIds(nextIds);
    setActiveSourceId(nextIds[0] ?? "");
  };

  const sendPrompt = () => {
    const trimmedPrompt = prompt.trim();
    if (
      !trimmedPrompt ||
      selectedSourceIds.length === 0 ||
      !activeSourceId ||
      sendMessageMutation.isPending
    ) {
      return;
    }

    setPendingPrompt(trimmedPrompt);
    setTransientError(null);
    sendMessageMutation.mutate(trimmedPrompt);
    setPrompt("");
  };

  const promptPlaceholder =
    selectedSourceIds.length > 1
      ? "Ask about revenue, churn, customers, surveys, or compare signals across selected sources..."
      : activeSource?.source_type === "spreadsheet"
        ? "Ask what Vayent notices in this spreadsheet..."
        : "Ask about this source, its schema, or any business question grounded in its data...";

  return (
    <div className="app-page workspace-layout">
      <section className="dashboard-hero workspace-hero">
        <div className="workspace-hero-copy">
          <p className="page-kicker">Workspace</p>
          <h1 className="display-title">Work across every selected source in one AI surface.</h1>
          <p className="page-text">
            Select databases, spreadsheets, or both. Vayent uses the active source
            as context and can combine selected business signals when the question
            asks for a cross-source answer.
          </p>
        </div>

        <div className="workspace-hero-pills">
          <div className="brand-pill">{selectedSources.length} selected sources</div>
          <div className="brand-pill">{messages.length} workspace turns</div>
          <div className="brand-pill" title={activeSource?.name ?? "Choose an active source"}>
            {activeSource?.name ?? "Choose an active source"}
          </div>
        </div>
      </section>

      <div className="workspace-grid">
        <section className="app-panel workspace-sources-panel">
          <div className="workspace-source-toolbar">
            <div className="workspace-panel-head">
              <div>
                <p className="page-kicker">Sources</p>
                <h2 className="workspace-panel-title">Choose workspace evidence</h2>
              </div>
              <div className="brand-badge">Multi-source</div>
            </div>

            <div className="workspace-source-controls">
              <div className="workspace-active-block">
                <label className="page-kicker" htmlFor="workspace-active-source">
                  Active Source
                </label>
                <div className="glass-select-wrap">
                  <select
                    id="workspace-active-source"
                    className="input glass-select"
                    value={activeSourceId}
                    onChange={(event) => setActiveSourceId(event.target.value)}
                    disabled={selectedSources.length === 0}
                    title={activeSource?.name ?? ""}
                  >
                    {selectedSources.length === 0 ? (
                      <option value="">Select a source first</option>
                    ) : null}
                    {selectedSources.map((source) => (
                      <option key={source.id} value={source.id}>
                        {source.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <Link to="/connections" className="brand-btn-secondary">
                Manage sources
              </Link>
            </div>
          </div>

          <div className="workspace-source-controls">
            <button
              type="button"
              className="brand-btn-secondary"
              onClick={() => applySourcePreset("database")}
              disabled={!sources.some((source) => source.source_type === "database")}
            >
              Database only
            </button>
            <button
              type="button"
              className="brand-btn-secondary"
              onClick={() => applySourcePreset("spreadsheet")}
              disabled={!sources.some((source) => source.source_type === "spreadsheet")}
            >
              Spreadsheet only
            </button>
            <button
              type="button"
              className="brand-btn-secondary"
              onClick={() => applySourcePreset("mixed")}
              disabled={
                !sources.some((source) => source.source_type === "database") ||
                !sources.some((source) => source.source_type === "spreadsheet")
              }
            >
              Mixed sources
            </button>
            <button
              type="button"
              className="brand-btn-primary"
              onClick={() => applySourcePreset("all")}
              disabled={sources.length === 0}
            >
              Select all
            </button>
          </div>

          <p className="workspace-panel-copy">
            Active in this conversation: {selectedDatabaseSources.length} database
            source{selectedDatabaseSources.length === 1 ? "" : "s"} and{" "}
            {selectedSpreadsheetSources.length} spreadsheet
            source{selectedSpreadsheetSources.length === 1 ? "" : "s"}.
          </p>

          {isLoading ? (
            <div className="app-empty workspace-empty">
              <p>Loading sources...</p>
            </div>
          ) : sources.length === 0 ? (
            <div className="app-empty workspace-empty">
              <p>No sources yet.</p>
              <p>Add a database or spreadsheet before launching the workspace.</p>
              <Link to="/connections" className="brand-btn-primary">
                Add a source
              </Link>
            </div>
          ) : (
            <div className="workspace-source-list app-scroll-panel">
              {sources.map((source) => {
                const isSelected = selectedSourceIds.includes(source.id);
                const isActive = activeSourceId === source.id;

                return (
                  <article
                    key={source.id}
                    className={`workspace-source-card ${
                      isSelected ? "workspace-source-card-selected" : ""
                    }`}
                  >
                    <div className="workspace-source-head">
                      <div>
                        <p className="workspace-source-title" title={source.name}>
                          {source.name}
                        </p>
                        <p className="workspace-source-meta" title={source.detail}>
                          {source.detail}
                        </p>
                      </div>

                      <button
                        type="button"
                        className={isSelected ? "brand-btn-secondary" : "brand-btn-primary"}
                        onClick={() => toggleSource(source.id)}
                      >
                        {isSelected ? "Remove" : "Add"}
                      </button>
                    </div>

                    <div className="workspace-source-tags">
                      <span className="brand-badge">{sourceKindLabel(source)}</span>
                      {isActive ? <span className="brand-pill">Active</span> : null}
                      {source.last_synced_at ? (
                        <span
                          className="brand-pill"
                          title={new Date(source.last_synced_at).toLocaleString()}
                        >
                          Synced {new Date(source.last_synced_at).toLocaleDateString()}
                        </span>
                      ) : (
                        <span className="brand-pill">Not synced</span>
                      )}
                    </div>

                    <div className="workspace-source-links">
                      {source.source_type === "database" ? (
                        <>
                          <Link to={`/connections/${source.id}/schema`}>View schema</Link>
                          <Link to={`/logs?connectionId=${source.id}`}>View logs</Link>
                        </>
                      ) : (
                        <Link to={`/dashboard?sourceId=${source.id}`}>View dashboard</Link>
                      )}
                      {isSelected && !isActive ? (
                        <button
                          type="button"
                          className="workspace-inline-link"
                          onClick={() => setActiveSourceId(source.id)}
                        >
                          Make active
                        </button>
                      ) : null}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </section>

        <section className="app-panel workspace-chat-panel">
          <div className="workspace-panel-head">
            <div>
              <p className="page-kicker">Workspace Chat</p>
              <h2 className="workspace-panel-title" title={activeSource?.name ?? ""}>
                {activeSource?.name ?? "Choose your working set"}
              </h2>
            </div>
            <div className="workspace-target-pills">
              {selectedSources.map((source) => (
                <span
                  key={source.id}
                  title={source.name}
                  className={`brand-pill ${
                    source.id === activeSourceId ? "workspace-pill-active" : ""
                  }`}
                >
                  {source.name}
                </span>
              ))}
            </div>
          </div>

          <p className="workspace-panel-copy">
            Vayent can answer from one source, compare multiple sources, or combine
            database metrics with spreadsheet context. Write operations stay in the
            single-database chat for safety.
          </p>

          <div className="workspace-message-list app-scroll-panel">
            {messages.length === 0 && !pendingPrompt ? (
              <div className="app-empty chat-empty">
                <p>Start a workspace conversation</p>
                <p>
                  Ask about revenue, churn, product segments, customer surveys, or
                  complaints and Vayent will use the selected sources in scope.
                </p>
              </div>
            ) : null}

            {messages.map((message) => {
              const statusClass =
                message.execution_status === "error"
                  ? "chat-status chat-status-error"
                  : message.execution_status === "executed"
                    ? "chat-status chat-status-success"
                    : "chat-status chat-status-pending";
              const targetedIds =
                message.targeted_source_ids && message.targeted_source_ids.length > 0
                  ? message.targeted_source_ids
                  : message.targeted_connection_ids;

              return (
                <React.Fragment key={message.id}>
                  <article className="chat-bubble chat-bubble-user">
                    <div className="chat-bubble-head">
                      <span>You</span>
                      <span>
                        {new Date(message.created_at).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                    </div>
                    <p>{message.user_prompt}</p>
                  </article>

                  <article
                    className={`chat-bubble chat-bubble-assistant ${
                      message.execution_status === "error" ? "chat-bubble-error" : ""
                    }`}
                  >
                    <div className="chat-bubble-head">
                      <span>Vayent Workspace</span>
                      <span className={statusClass}>{message.execution_status}</span>
                    </div>

                    <p className="chat-assistant-copy">
                      {message.ai_explanation || "No response was generated."}
                    </p>

                    {targetedIds.length > 0 ? (
                      <div className="workspace-result-tags">
                        {targetedIds.map((sourceId) => (
                          <span
                            key={sourceId}
                            className="brand-pill"
                            title={sourceLookup.get(sourceId)?.name ?? sourceId}
                          >
                            {sourceLookup.get(sourceId)?.name ?? sourceId}
                          </span>
                        ))}
                      </div>
                    ) : null}

                    {message.warnings.length > 0 ? (
                      <div className="workspace-warning-list">
                        {message.warnings.map((warning) => (
                          <div
                            key={`${message.id}-${warning}`}
                            className="chat-error"
                            title={warning}
                          >
                            {warning}
                          </div>
                        ))}
                      </div>
                    ) : null}

                    {message.generated_queries.length > 0 ||
                    message.query_results.length > 0 ? (
                      <details className="chat-details">
                        <summary>Technical details</summary>

                        {message.generated_queries.length > 0 ? (
                          <div className="chat-block">
                            <p className="page-kicker">Generated analysis</p>
                            <div className="workspace-result-stack">
                              {message.generated_queries.map((query) => {
                                const querySourceId = query.source_id ?? query.connection_id;
                                return (
                                  <div
                                    key={`${message.id}-${querySourceId}`}
                                    className="workspace-result-card"
                                  >
                                    <div className="workspace-result-card-head">
                                      <div>
                                        <p
                                          className="workspace-result-title"
                                          title={query.connection_name}
                                        >
                                          {query.connection_name}
                                        </p>
                                        <p
                                          className="workspace-result-meta"
                                          title={query.database_name}
                                        >
                                          {query.database_name}
                                        </p>
                                      </div>
                                      <span
                                        className={`chat-status ${
                                          query.status === "executed"
                                            ? "chat-status-success"
                                            : "chat-status-error"
                                        }`}
                                      >
                                        {query.status}
                                      </span>
                                    </div>
                                    <pre className="code-block app-scroll-x">{query.sql}</pre>
                                    {query.error ? (
                                      <div className="chat-error" title={query.error}>
                                        {query.error}
                                      </div>
                                    ) : null}
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        ) : null}

                        {message.query_results.length > 0 ? (
                          <div className="chat-block">
                            <p className="page-kicker">Results</p>
                            <div className="workspace-result-stack">
                              {message.query_results.map((result) => {
                                const resultSourceId = result.source_id ?? result.connection_id;
                                return (
                                  <div
                                    key={`${message.id}-${resultSourceId}-result`}
                                    className="workspace-result-card"
                                  >
                                    <div className="workspace-result-card-head">
                                      <div>
                                        <p
                                          className="workspace-result-title"
                                          title={result.connection_name}
                                        >
                                          {result.connection_name}
                                        </p>
                                        <p className="workspace-result-meta">
                                          {result.row_count} row
                                          {result.row_count === 1 ? "" : "s"}
                                          {result.truncated ? " - preview" : ""}
                                        </p>
                                      </div>
                                      {result.source_type === "spreadsheet" ? (
                                        <Link
                                          to={`/dashboard?sourceId=${resultSourceId}`}
                                          className="workspace-inline-link"
                                        >
                                          Dashboard
                                        </Link>
                                      ) : (
                                        <Link
                                          to={`/logs?connectionId=${result.connection_id}`}
                                          className="workspace-inline-link"
                                        >
                                          View logs
                                        </Link>
                                      )}
                                    </div>
                                    {result.error ? (
                                      <div className="chat-error" title={result.error}>
                                        {result.error}
                                      </div>
                                    ) : (
                                      <pre className="code-block app-scroll-x">
                                        {JSON.stringify(result.rows, null, 2)}
                                      </pre>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        ) : null}
                      </details>
                    ) : null}
                  </article>
                </React.Fragment>
              );
            })}

            {pendingPrompt ? (
              <article className="chat-bubble chat-bubble-user">
                <div className="chat-bubble-head">
                  <span>You</span>
                  <span>sending</span>
                </div>
                <p>{pendingPrompt}</p>
              </article>
            ) : null}

            {transientError ? (
              <article className="chat-bubble chat-bubble-assistant chat-bubble-error">
                <div className="chat-bubble-head">
                  <span>Vayent Workspace</span>
                  <span className="chat-status chat-status-error">error</span>
                </div>
                <p className="chat-assistant-copy">{transientError}</p>
              </article>
            ) : null}
          </div>
        </section>

        <section className="app-panel chat-composer workspace-composer">
          <div className="chat-composer-head">
            <p className="page-kicker">Compose</p>
            <p className="chat-composer-note">
              Vayent uses selected source context, then falls back to the active
              source when multiple options look equally relevant.
            </p>
          </div>

          <div className="chat-input-row">
            <input
              className="input chat-composer-input"
              placeholder={promptPlaceholder}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  sendPrompt();
                }
              }}
              disabled={sendMessageMutation.isPending || selectedSourceIds.length === 0}
            />

            <button
              type="button"
              className="brand-btn-primary"
              onClick={sendPrompt}
              disabled={
                sendMessageMutation.isPending ||
                selectedSourceIds.length === 0 ||
                !activeSourceId
              }
            >
              {sendMessageMutation.isPending ? "Sending..." : "Send"}
            </button>
          </div>
        </section>
      </div>
    </div>
  );
};

export default WorkspacePage;
