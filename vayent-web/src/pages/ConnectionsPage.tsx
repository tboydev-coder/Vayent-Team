import React, { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "../services/api";
import {
  getActiveConnectionId,
  setActiveConnectionId,
} from "../utils/activeConnection";
import "../styles/connections.css";
import type {
  ChatSession,
  ConnectedSource,
  ConnectedSourceList,
  ConnectionSslMode,
  DatabaseType,
  SpreadsheetSource,
  SyncSourceResponse,
} from "../types";

const defaultPorts: Record<DatabaseType, string> = {
  postgresql: "5432",
  mysql: "3306",
};

const sslModeLabels: Record<ConnectionSslMode, string> = {
  require: "SSL required",
  prefer: "SSL preferred",
  disable: "SSL disabled",
};

const allowedSpreadsheetExtensions = new Set([".xlsx", ".xls", ".csv"]);
const allowedSpreadsheetMimeTypes = new Set([
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel",
  "text/csv",
  "application/csv",
  "text/plain",
  "application/octet-stream",
  "",
]);

interface OpenSourceResult {
  source: ConnectedSource;
  session?: ChatSession;
}

const formatDateTime = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "Not synced yet";

const extensionFromFilename = (filename: string) => {
  const dotIndex = filename.lastIndexOf(".");
  return dotIndex >= 0 ? filename.slice(dotIndex).toLowerCase() : "";
};

const validateSpreadsheetFile = (file: File | null): string | null => {
  if (!file) {
    return "Choose an Excel or CSV file to upload.";
  }

  const extension = extensionFromFilename(file.name);
  if (!allowedSpreadsheetExtensions.has(extension)) {
    return "Unsupported file type. Upload .xlsx, .xls, or .csv only.";
  }

  const normalizedType = file.type.split(";")[0].trim().toLowerCase();
  if (!allowedSpreadsheetMimeTypes.has(normalizedType)) {
    return "Unsupported upload format. PDFs, images, Word files, ZIP files, and executables are not accepted.";
  }

  return null;
};

const validateSpreadsheetUrl = (url: string): string | null => {
  try {
    const parsed = new URL(url.trim());
    if (!["http:", "https:"].includes(parsed.protocol)) {
      return "Enter a valid http or https spreadsheet URL.";
    }
    return null;
  } catch {
    return "Enter a valid spreadsheet URL.";
  }
};

const getSourceMark = (source: ConnectedSource) => {
  if (source.source_type === "database") {
    return source.source_kind === "postgresql" ? "PG" : "MY";
  }

  const fileType =
    typeof source.metadata.file_type === "string"
      ? source.metadata.file_type
      : source.source_kind;
  return fileType === "csv" ? "CSV" : "XL";
};

const getSourceTypeLine = (source: ConnectedSource) => {
  if (source.source_type === "database") {
    const sslMode =
      typeof source.metadata.ssl_mode === "string"
        ? source.metadata.ssl_mode
        : null;
    const sslLabel = sslMode
      ? sslModeLabels[sslMode as ConnectionSslMode] ?? sslMode
      : null;
    return `${source.source_kind}${sslLabel ? ` - ${sslLabel}` : ""}`;
  }

  const provider =
    typeof source.metadata.source_provider === "string"
      ? source.metadata.source_provider.replaceAll("_", " ")
      : source.source_kind;
  return `${source.source_kind} - ${provider}`;
};

const ConnectionsPage: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [feedback, setFeedback] = useState<string | null>(null);
  const [feedbackTone, setFeedbackTone] = useState<"success" | "error">("success");
  const [sourceType, setSourceType] = useState<"database" | "spreadsheet">("database");
  const [form, setForm] = useState({
    name: "",
    db_type: "postgresql" as DatabaseType,
    host: "",
    port: defaultPorts.postgresql,
    database_name: "",
    username: "",
    password: "",
    ssl_mode: "require" as ConnectionSslMode,
  });
  const [uploadName, setUploadName] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [linkName, setLinkName] = useState("");
  const [linkUrl, setLinkUrl] = useState("");

  const { data: sourceList, isLoading } = useQuery<ConnectedSourceList>({
    queryKey: ["connected-sources"],
    queryFn: async () => {
      const res = await api.get("/connections/sources");
      return res.data as ConnectedSourceList;
    },
  });

  const sources = useMemo(() => sourceList?.items ?? [], [sourceList?.items]);
  const databaseSources = useMemo(
    () => sources.filter((source) => source.source_type === "database"),
    [sources],
  );

  const createConnectionMutation = useMutation<void, Error>({
    mutationFn: async () => {
      await api.post("/connections", {
        ...form,
        port: Number(form.port),
      });
    },
    onSuccess: () => {
      setFeedback("Connection created and schema synced successfully.");
      setFeedbackTone("success");
      queryClient.invalidateQueries({ queryKey: ["connections"] });
      queryClient.invalidateQueries({ queryKey: ["connected-sources"] });
      setForm({
        name: "",
        db_type: "postgresql",
        host: "",
        port: defaultPorts.postgresql,
        database_name: "",
        username: "",
        password: "",
        ssl_mode: "require",
      });
    },
    onError: (error) => {
      setFeedback(error.message);
      setFeedbackTone("error");
    },
  });

  const uploadSpreadsheetMutation = useMutation<SpreadsheetSource, Error>({
    mutationFn: async () => {
      const validationError = validateSpreadsheetFile(uploadFile);
      if (validationError) {
        throw new Error(validationError);
      }

      const body = new FormData();
      body.append("name", uploadName.trim() || uploadFile?.name || "Spreadsheet");
      body.append("file", uploadFile as File);
      const res = await api.post("/connections/spreadsheets/upload", body, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      return res.data as SpreadsheetSource;
    },
    onSuccess: (source) => {
      setFeedback("Spreadsheet uploaded and profiled.");
      setFeedbackTone("success");
      setUploadName("");
      setUploadFile(null);
      queryClient.invalidateQueries({ queryKey: ["connected-sources"] });
      setActiveConnectionId(source.id);
      navigate(`/chat/source/${source.id}`);
    },
    onError: (error) => {
      setFeedback(error.message);
      setFeedbackTone("error");
    },
  });

  const linkSpreadsheetMutation = useMutation<SpreadsheetSource, Error>({
    mutationFn: async () => {
      const validationError = validateSpreadsheetUrl(linkUrl);
      if (validationError) {
        throw new Error(validationError);
      }

      const res = await api.post("/connections/spreadsheets/link", {
        name: linkName.trim() || "Spreadsheet link",
        url: linkUrl.trim(),
      });
      return res.data as SpreadsheetSource;
    },
    onSuccess: (source) => {
      setFeedback("Spreadsheet link connected and profiled.");
      setFeedbackTone("success");
      setLinkName("");
      setLinkUrl("");
      queryClient.invalidateQueries({ queryKey: ["connected-sources"] });
      setActiveConnectionId(source.id);
      navigate(`/chat/source/${source.id}`);
    },
    onError: (error) => {
      setFeedback(error.message);
      setFeedbackTone("error");
    },
  });

  const startChatMutation = useMutation<OpenSourceResult, Error, ConnectedSource>({
    mutationFn: async (source) => {
      if (source.source_type === "spreadsheet") {
        return { source };
      }
      const res = await api.post("/chat/sessions", {
        connection_id: source.id,
        title: source.name,
      });
      return { source, session: res.data as ChatSession };
    },
    onSuccess: ({ source, session }) => {
      setActiveConnectionId(source.id);
      if (source.source_type === "spreadsheet") {
        navigate(`/chat/source/${source.id}`);
        return;
      }
      if (session) {
        navigate(`/chat/${session.id}`);
      }
    },
    onError: (error) => {
      setFeedback(error.message);
      setFeedbackTone("error");
    },
  });

  const syncSourceMutation = useMutation<
    SyncSourceResponse,
    Error,
    ConnectedSource
  >({
    mutationFn: async (source) => {
      const res = await api.post(`/connections/sources/${source.id}/sync`);
      return res.data as SyncSourceResponse;
    },
    onSuccess: (response) => {
      setFeedback(response.message);
      setFeedbackTone("success");
      queryClient.invalidateQueries({ queryKey: ["connections"] });
      queryClient.invalidateQueries({ queryKey: ["connected-sources"] });
      queryClient.invalidateQueries({ queryKey: ["copilot-artifacts"] });
    },
    onError: (error) => {
      setFeedback(error.message);
      setFeedbackTone("error");
    },
  });

  const renameSourceMutation = useMutation<
    ConnectedSource,
    Error,
    { source: ConnectedSource; name: string }
  >({
    mutationFn: async ({ source, name }) => {
      const res = await api.patch(`/connections/sources/${source.id}/rename`, {
        name,
      });
      return res.data as ConnectedSource;
    },
    onSuccess: () => {
      setFeedback("Source renamed.");
      setFeedbackTone("success");
      queryClient.invalidateQueries({ queryKey: ["connections"] });
      queryClient.invalidateQueries({ queryKey: ["connected-sources"] });
    },
    onError: (error) => {
      setFeedback(error.message);
      setFeedbackTone("error");
    },
  });

  const disconnectSourceMutation = useMutation<void, Error, ConnectedSource>({
    mutationFn: async (source) => {
      await api.delete(`/connections/sources/${source.id}`);
    },
    onSuccess: (_, source) => {
      if (source.source_type === "database" && getActiveConnectionId() === source.id) {
        setActiveConnectionId(null);
      }
      setFeedback("Source disconnected.");
      setFeedbackTone("success");
      queryClient.invalidateQueries({ queryKey: ["connections"] });
      queryClient.invalidateQueries({ queryKey: ["connected-sources"] });
    },
    onError: (error) => {
      setFeedback(error.message);
      setFeedbackTone("error");
    },
  });

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    setFeedback(null);
    createConnectionMutation.mutate();
  };

  const handleUploadSubmit = () => {
    setFeedback(null);
    const validationError = validateSpreadsheetFile(uploadFile);
    if (validationError) {
      setFeedback(validationError);
      setFeedbackTone("error");
      return;
    }
    uploadSpreadsheetMutation.mutate();
  };

  const handleLinkSubmit = () => {
    setFeedback(null);
    const validationError = validateSpreadsheetUrl(linkUrl);
    if (validationError) {
      setFeedback(validationError);
      setFeedbackTone("error");
      return;
    }
    linkSpreadsheetMutation.mutate();
  };

  const selectedSslLabel = sslModeLabels[form.ssl_mode];
  const openingSourceId =
    startChatMutation.isPending && startChatMutation.variables
      ? startChatMutation.variables.id
      : null;

  const renderFeedback = () => {
    if (!feedback) {
      return null;
    }

    return (
      <p
        className={
          feedbackTone === "success"
            ? "connections-feedback connections-feedback-success"
            : "connections-error"
        }
      >
        {feedback}
      </p>
    );
  };

  return (
    <div className="app-page connections-layout">
      <section className="dashboard-hero">
        <div className="connections-hero-grid">
          <div className="connections-copy">
            <p className="page-kicker">Connection Studio</p>
            <h1 className="display-title">
              Bring every business source into the Vayent workspace.
            </h1>
            <p className="page-text">
              Add databases, uploaded spreadsheets, and shareable Excel links so
              Vayent can profile the evidence and turn it into workspace answers.
            </p>
          </div>

          <div className="connections-hero-meta">
            <p>Active sources</p>
            <p>{sources.length}</p>
          </div>
        </div>
      </section>

      <div className="connections-grid">
        <section className="app-panel-strong connections-panel">
          <div className="connections-panel-head">
            <div>
              <p className="page-kicker">New Source</p>
              <h2 className="connections-panel-title">Create connection</h2>
            </div>
            <div className="brand-badge">
              {sourceType === "database" ? selectedSslLabel : "Excel ready"}
            </div>
          </div>

          <div className="connections-source-picker" aria-label="Source type">
            <button
              type="button"
              className={
                sourceType === "database"
                  ? "connections-source-option connections-source-option-active"
                  : "connections-source-option"
              }
              onClick={() => setSourceType("database")}
            >
              Database
            </button>
            <button
              type="button"
              className={
                sourceType === "spreadsheet"
                  ? "connections-source-option connections-source-option-active"
                  : "connections-source-option"
              }
              onClick={() => setSourceType("spreadsheet")}
            >
              Excel Spreadsheet
            </button>
          </div>

          {sourceType === "database" ? (
            <form onSubmit={handleSubmit} className="connections-form">
              <input
                placeholder="Connection name"
                value={form.name}
                onChange={(event) =>
                  setForm((current) => ({ ...current, name: event.target.value }))
                }
                className="input"
                required
              />

              <div className="connections-form-grid">
                <div className="glass-select-wrap">
                  <select
                    value={form.db_type}
                    onChange={(event) => {
                      const dbType = event.target.value as DatabaseType;
                      setForm((current) => ({
                        ...current,
                        db_type: dbType,
                        port: defaultPorts[dbType],
                      }));
                    }}
                    className="input glass-select"
                  >
                    <option value="postgresql">PostgreSQL</option>
                    <option value="mysql">MySQL</option>
                  </select>
                </div>

                <input
                  placeholder="Port"
                  value={form.port}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, port: event.target.value }))
                  }
                  className="input"
                  inputMode="numeric"
                  required
                />
              </div>

              <div className="glass-select-wrap">
                <select
                  value={form.ssl_mode}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      ssl_mode: event.target.value as ConnectionSslMode,
                    }))
                  }
                  className="input glass-select"
                >
                  <option value="require">SSL required</option>
                  <option value="prefer">SSL preferred</option>
                  <option value="disable">SSL disabled</option>
                </select>
              </div>

              <input
                placeholder="Host"
                value={form.host}
                onChange={(event) =>
                  setForm((current) => ({ ...current, host: event.target.value }))
                }
                className="input"
                required
              />

              <input
                placeholder="Database name"
                value={form.database_name}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    database_name: event.target.value,
                  }))
                }
                className="input"
                required
              />

              <div className="connections-form-grid">
                <input
                  placeholder="Username"
                  value={form.username}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, username: event.target.value }))
                  }
                  className="input"
                  required
                />
                <input
                  type="password"
                  placeholder="Password"
                  value={form.password}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, password: event.target.value }))
                  }
                  className="input"
                  required
                />
              </div>

              {createConnectionMutation.error ? (
                <p className="connections-error">
                  {createConnectionMutation.error.message}
                </p>
              ) : null}

              <button
                type="submit"
                disabled={createConnectionMutation.isPending}
                className="brand-btn-primary"
              >
                {createConnectionMutation.isPending ? "Creating..." : "Create connection"}
              </button>
            </form>
          ) : (
            <div className="connections-spreadsheet-methods">
              <div className="connections-method-card">
                <div>
                  <p className="page-kicker">Method A</p>
                  <h3>Upload Excel file</h3>
                </div>
                <input
                  placeholder="Source name"
                  value={uploadName}
                  onChange={(event) => setUploadName(event.target.value)}
                  className="input"
                />
                <input
                  type="file"
                  accept=".xlsx,.xls,.csv,text/csv,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                  onChange={(event) => {
                    const nextFile = event.target.files?.[0] ?? null;
                    const validationError = validateSpreadsheetFile(nextFile);
                    if (validationError) {
                      setFeedback(validationError);
                      setFeedbackTone("error");
                      setUploadFile(null);
                      event.currentTarget.value = "";
                      return;
                    }
                    setFeedback(null);
                    setUploadFile(nextFile);
                  }}
                  className="input"
                />
                <button
                  type="button"
                  className="brand-btn-primary"
                  disabled={uploadSpreadsheetMutation.isPending}
                  onClick={handleUploadSubmit}
                >
                  {uploadSpreadsheetMutation.isPending ? "Uploading..." : "Upload spreadsheet"}
                </button>
              </div>

              <div className="connections-method-card">
                <div>
                  <p className="page-kicker">Method B</p>
                  <h3>Excel link</h3>
                </div>
                <input
                  placeholder="Source name"
                  value={linkName}
                  onChange={(event) => setLinkName(event.target.value)}
                  className="input"
                />
                <input
                  placeholder="https://..."
                  value={linkUrl}
                  onChange={(event) => setLinkUrl(event.target.value)}
                  className="input"
                  inputMode="url"
                />
                <button
                  type="button"
                  className="brand-btn-primary"
                  disabled={linkSpreadsheetMutation.isPending}
                  onClick={handleLinkSubmit}
                >
                  {linkSpreadsheetMutation.isPending ? "Connecting..." : "Connect link"}
                </button>
              </div>
            </div>
          )}

          {renderFeedback()}
        </section>

        <section className="app-panel connections-panel">
          <div className="connections-panel-head">
            <div>
              <p className="page-kicker">Connected Sources</p>
              <h2 className="connections-panel-title">Unified source estate</h2>
            </div>
            <Link to="/workspace" className="brand-btn-secondary">
              Open workspace
            </Link>
          </div>

          {isLoading ? (
            <div className="app-empty connections-empty">
              <p>Loading sources...</p>
            </div>
          ) : sources.length === 0 ? (
            <div className="app-empty connections-empty">
              <p>No sources yet</p>
              <p>Create a database or spreadsheet source from the panel on the left.</p>
            </div>
          ) : (
            <div className="connections-list">
              {sources.map((source) => {
                const isDatabase = source.source_type === "database";
                const isSyncing =
                  syncSourceMutation.isPending &&
                  syncSourceMutation.variables?.id === source.id;
                const isDisconnecting =
                  disconnectSourceMutation.isPending &&
                  disconnectSourceMutation.variables?.id === source.id;
                const isRenaming =
                  renameSourceMutation.isPending &&
                  renameSourceMutation.variables?.source.id === source.id;
                const isOpening = openingSourceId === source.id;

                return (
                  <div
                    key={source.id}
                    className={`connections-card ${
                      isOpening ? "connections-card-opening" : ""
                    }`}
                  >
                    <div className="connections-card-head">
                      <div className="connections-card-main">
                        <div className="connections-card-mark">
                          {getSourceMark(source)}
                        </div>
                        <div>
                          <p className="connections-card-title" title={source.name}>
                            {source.name}
                          </p>
                          <p className="connections-card-meta" title={source.detail}>
                            {source.detail}
                          </p>
                          <p className="connections-card-type" title={getSourceTypeLine(source)}>
                            {getSourceTypeLine(source)}
                          </p>
                        </div>
                      </div>

                      <div className="connections-card-actions">
                        <button
                          type="button"
                          onClick={() => {
                            if (!startChatMutation.isPending) {
                              startChatMutation.mutate(source);
                            }
                          }}
                          className="brand-btn-primary"
                          disabled={isOpening || isSyncing || isDisconnecting}
                          aria-busy={isOpening}
                        >
                          {isOpening ? "Opening..." : "Start chat"}
                        </button>

                        {isDatabase ? (
                          <Link
                            to={`/connections/${source.id}/schema`}
                            className="brand-btn-secondary"
                            onClick={() => setActiveConnectionId(source.id)}
                          >
                            View
                          </Link>
                        ) : (
                          <Link
                            to={`/dashboard?sourceId=${source.id}`}
                            className="brand-btn-secondary"
                          >
                            View
                          </Link>
                        )}

                        <button
                          type="button"
                          onClick={() => syncSourceMutation.mutate(source)}
                          className="brand-btn-secondary"
                          disabled={isSyncing || isDisconnecting}
                        >
                          {isSyncing
                            ? "Syncing..."
                            : source.source_type === "spreadsheet" &&
                                source.source_kind === "link"
                              ? "Sync link"
                              : "Refresh"}
                        </button>

                        <button
                          type="button"
                          className="brand-btn-secondary"
                          disabled={isRenaming || isDisconnecting}
                          onClick={() => {
                            const nextName = window.prompt("Rename source", source.name);
                            if (nextName?.trim()) {
                              renameSourceMutation.mutate({
                                source,
                                name: nextName.trim(),
                              });
                            }
                          }}
                        >
                          {isRenaming ? "Renaming..." : "Rename"}
                        </button>

                        <button
                          type="button"
                          className="brand-btn-danger"
                          disabled={isDisconnecting || isSyncing}
                          onClick={() => {
                            const confirmed = window.confirm(
                              `Disconnect "${source.name}"?`,
                            );
                            if (confirmed) {
                              disconnectSourceMutation.mutate(source);
                            }
                          }}
                        >
                          {isDisconnecting ? "Disconnecting..." : "Disconnect"}
                        </button>
                      </div>
                    </div>

                    <div className="connections-card-note">
                      <span title={`Created ${formatDateTime(source.created_at)}`}>
                        Created {new Date(source.created_at).toLocaleDateString()}
                      </span>
                      <span title={`Last sync ${formatDateTime(source.last_synced_at)}`}>
                        Last sync {formatDateTime(source.last_synced_at)}
                      </span>
                      <span title={source.status_message ?? source.status}>
                        {source.status_message ?? source.status}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {databaseSources.length > 0 ? null : (
            <p className="connections-card-type">
              Spreadsheets are ready for chat, Workspace, and Dashboard analysis.
            </p>
          )}
        </section>
      </div>
    </div>
  );
};

export default ConnectionsPage;
