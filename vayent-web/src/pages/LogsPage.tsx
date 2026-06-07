import React, { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import api from "../services/api";
import {
  getActiveConnectionId,
  setActiveConnectionId,
} from "../utils/activeConnection";
import "../styles/logs.css";
import type { Connection, QueryLog, QueryLogPage } from "../types";

const PAGE_SIZE = 10;
const ALL_DATABASES_VALUE = "__all__";

interface LogGroup {
  key: string;
  label: string;
  items: QueryLog[];
}

const dateGroupFormatter = new Intl.DateTimeFormat(undefined, {
  month: "long",
  day: "numeric",
  year: "numeric",
});

const getDateKey = (value: string): string => {
  const date = new Date(value);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};

const getDateLabel = (value: string): string => {
  const date = new Date(value);
  const target = new Date(date);
  target.setHours(0, 0, 0, 0);

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);

  if (target.getTime() === today.getTime()) {
    return "Today";
  }

  if (target.getTime() === yesterday.getTime()) {
    return "Yesterday";
  }

  return dateGroupFormatter.format(date);
};

const LogsPage: React.FC = () => {
  const [page, setPage] = useState(1);
  const [searchParams] = useSearchParams();
  const requestedConnectionId = searchParams.get("connectionId");
  const [selectedConnectionId, setSelectedConnectionId] = useState("");
  const [expandedLogId, setExpandedLogId] = useState<string | null>(null);

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
    if (connectionsLoading) {
      return;
    }

    if (selectedConnectionId === ALL_DATABASES_VALUE) {
      return;
    }

    const validConnectionIds = new Set(connections.map((connection) => connection.id));

    if (selectedConnectionId && validConnectionIds.has(selectedConnectionId)) {
      return;
    }

    const storedConnectionId = getActiveConnectionId();
    const sortedConnections = [...connections].sort(
      (left, right) =>
        new Date(right.created_at).getTime() - new Date(left.created_at).getTime(),
    );

    const nextConnectionId =
      [requestedConnectionId, storedConnectionId, sortedConnections[0]?.id].find(
        (connectionId): connectionId is string =>
          Boolean(connectionId && validConnectionIds.has(connectionId)),
      ) ?? "";

    if (nextConnectionId) {
      setSelectedConnectionId(nextConnectionId);
      setActiveConnectionId(nextConnectionId);
      return;
    }

    if (!selectedConnectionId) {
      setSelectedConnectionId(ALL_DATABASES_VALUE);
    }
  }, [connections, connectionsLoading, requestedConnectionId, selectedConnectionId]);

  const effectiveConnectionId =
    selectedConnectionId && selectedConnectionId !== ALL_DATABASES_VALUE
      ? selectedConnectionId
      : undefined;
  const awaitingDefaultSelection =
    !connectionsLoading && connections.length > 0 && selectedConnectionId === "";

  const logsReady =
    !connectionsLoading &&
    !awaitingDefaultSelection &&
    (connections.length === 0 || selectedConnectionId !== "");

  const { data, isLoading } = useQuery<QueryLogPage>({
    queryKey: ["logs", effectiveConnectionId ?? ALL_DATABASES_VALUE, page],
    enabled: logsReady,
    queryFn: async () => {
      const res = await api.get("/chat/query-logs/paginated", {
        params: {
          page,
          page_size: PAGE_SIZE,
          connection_id: effectiveConnectionId,
        },
      });
      return res.data as QueryLogPage;
    },
  });

  const logs = useMemo(() => data?.items ?? [], [data?.items]);
  const totalPages = data?.total_pages ?? 1;
  const selectedConnection =
    connections.find((connection) => connection.id === effectiveConnectionId) ?? null;

  const connectionLookup = useMemo(
    () => new Map(connections.map((connection) => [connection.id, connection])),
    [connections],
  );

  const groupedLogs = useMemo<LogGroup[]>(() => {
    const groups: LogGroup[] = [];
    const groupLookup = new Map<string, LogGroup>();

    logs.forEach((log) => {
      const key = getDateKey(log.executed_at);
      const existingGroup = groupLookup.get(key);

      if (existingGroup) {
        existingGroup.items.push(log);
        return;
      }

      const nextGroup = {
        key,
        label: getDateLabel(log.executed_at),
        items: [log],
      };

      groupLookup.set(key, nextGroup);
      groups.push(nextGroup);
    });

    return groups;
  }, [logs]);

  const visiblePages = useMemo(() => {
    const pages = [];
    const start = Math.max(1, page - 2);
    const end = Math.min(totalPages, start + 4);

    for (let current = start; current <= end; current += 1) {
      pages.push(current);
    }

    return pages;
  }, [page, totalPages]);

  const handleConnectionChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    const nextConnectionId = event.target.value;
    setSelectedConnectionId(nextConnectionId);
    setPage(1);

    if (nextConnectionId !== ALL_DATABASES_VALUE) {
      setActiveConnectionId(nextConnectionId);
    }
  };

  const activeSourceLabel = selectedConnection ? selectedConnection.name : "All databases";
  const activeSourceDescription = selectedConnection
    ? `${selectedConnection.database_name} on ${selectedConnection.host}:${selectedConnection.port}`
    : "Review the execution trail across every connected source.";

  return (
    <div className="app-page logs-layout">
      <section className="dashboard-hero">
        <div className="logs-hero">
          <div className="logs-copy">
            <p className="page-kicker">Execution History</p>
            <h1 className="display-title">Query logs and operational trace.</h1>
            <p className="page-text">
              Switch between databases, stay anchored to the active source by
              default, and scan the audit trail in day-based groups that are easier
              to navigate.
            </p>
          </div>

          <div className="logs-hero-pills">
            <div className="brand-pill">{data?.total_items ?? 0} filtered entries</div>
            <div className="brand-pill">{groupedLogs.length} day groups</div>
            <div className="brand-pill">{activeSourceLabel}</div>
          </div>
        </div>
      </section>

      <section className="app-panel logs-panel">
        <div className="logs-toolbar">
          <div className="logs-filter-block">
            <label className="page-kicker" htmlFor="logs-connection-filter">
              Database Filter
            </label>
            <div className="glass-select-wrap logs-select-wrap">
              <select
                id="logs-connection-filter"
                className="input glass-select"
                value={selectedConnectionId}
                onChange={handleConnectionChange}
                disabled={connectionsLoading || connections.length === 0}
              >
                <option value={ALL_DATABASES_VALUE}>All databases</option>
                {connections.map((connection) => (
                  <option key={connection.id} value={connection.id}>
                    {connection.name} ({connection.db_type})
                  </option>
                ))}
              </select>
            </div>
            <p className="logs-filter-note">{activeSourceDescription}</p>
          </div>

          <div className="logs-toolbar-summary">
            <p className="page-kicker">View State</p>
            <p className="logs-toolbar-title">{activeSourceLabel}</p>
            <p className="logs-toolbar-copy">
              {selectedConnection
                ? "Showing the selected database first so query review stays scoped."
                : "Showing all databases. Each row still keeps its source visible."}
            </p>
          </div>
        </div>

        {isLoading || connectionsLoading || awaitingDefaultSelection ? (
          <div className="app-empty logs-empty">
            <p>Loading logs...</p>
          </div>
        ) : logs.length === 0 ? (
          <div className="app-empty logs-empty">
            <p>No query logs yet</p>
            <p>
              {selectedConnection
                ? `Run queries against ${selectedConnection.name} and Vayent will populate this execution history.`
                : "Start using chat and Vayent will populate this execution history."}
            </p>
          </div>
        ) : (
          <>
            <div className="logs-table-wrap app-scroll-x">
              <table className="logs-table">
                <thead>
                  <tr>
                    <th>Query</th>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Time</th>
                    <th>Rows</th>
                    <th>Executed</th>
                  </tr>
                </thead>

                {groupedLogs.map((group) => (
                  <tbody key={group.key}>
                    <tr className="logs-group-row">
                      <th colSpan={6}>
                        <div className="logs-group-head">
                          <span className="logs-group-title">{group.label}</span>
                          <span className="logs-group-count">
                            {group.items.length} {group.items.length === 1 ? "entry" : "entries"}
                          </span>
                        </div>
                      </th>
                    </tr>

                    {group.items.map((log) => {
                      const connection = connectionLookup.get(log.connection_id);
                      const isExpanded = expandedLogId === log.id;

                      return (
                        <React.Fragment key={log.id}>
                          <tr
                            className={isExpanded ? "logs-row-active" : undefined}
                            onClick={() =>
                              setExpandedLogId((current) =>
                                current === log.id ? null : log.id,
                              )
                            }
                            style={{ cursor: "pointer" }}
                          >
                            <td>
                            <div className="logs-query">
                              <div className="logs-query-main">{log.query_text}</div>
                              <div className="logs-query-meta">
                                <span className="logs-query-source">
                                  {connection?.name ?? "Unknown database"}
                                </span>
                                {connection ? (
                                  <span>
                                    {connection.database_name} on {connection.host}:{connection.port}
                                  </span>
                                ) : null}
                              </div>
                              {log.error_message ? (
                                <div className="logs-query-error">{log.error_message}</div>
                              ) : null}
                            </div>
                          </td>
                          <td className="logs-cell">{log.query_type}</td>
                          <td>
                            <span
                              className={`logs-status ${
                                log.status === "success"
                                  ? "logs-status-success"
                                  : log.status === "error"
                                    ? "logs-status-error"
                                    : "logs-status-pending"
                              }`}
                            >
                              {log.status}
                            </span>
                          </td>
                          <td className="logs-cell">{log.execution_time_ms ?? "-"} ms</td>
                          <td className="logs-cell">{log.row_count ?? "-"}</td>
                          <td className="logs-cell">
                            {new Date(log.executed_at).toLocaleString()}
                          </td>
                          </tr>

                          {isExpanded ? (
                            <tr className="logs-row-details">
                              <td colSpan={6}>
                                <div className="logs-details">
                                  <p className="page-kicker">Executed SQL</p>
                                  <pre className="code-block app-scroll-x">
                                    {log.query_text}
                                  </pre>
                                </div>
                              </td>
                            </tr>
                          ) : null}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                ))}
              </table>
            </div>

            <div className="logs-pagination">
              <button
                type="button"
                className="brand-btn-secondary"
                disabled={page === 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
              >
                Previous
              </button>

              <div className="logs-pagination-pages">
                {visiblePages.map((pageNumber) => (
                  <button
                    key={pageNumber}
                    type="button"
                    className={`logs-page-button ${
                      pageNumber === page ? "logs-page-button-active" : ""
                    }`}
                    onClick={() => setPage(pageNumber)}
                  >
                    {pageNumber}
                  </button>
                ))}
              </div>

              <button
                type="button"
                className="brand-btn-secondary"
                disabled={page >= totalPages}
                onClick={() =>
                  setPage((current) => Math.min(totalPages, current + 1))
                }
              >
                Next
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
};

export default LogsPage;
