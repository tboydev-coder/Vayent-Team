import React, { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "../services/api";
import { setActiveConnectionId } from "../utils/activeConnection";
import "../styles/chatPage.css";
import type {
  ChatSession,
  ConnectedSource,
  ConnectedSourceList,
  SpreadsheetSource,
  WorkspaceHistoryMessage,
  WorkspaceMessage,
} from "../types";

const SPREADSHEET_CHAT_STORAGE_PREFIX = "vayent_spreadsheet_chat_";
const CHAT_TYPING_MIN_DURATION_MS = 1200;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const asText = (value: unknown, fallback = ""): string =>
  typeof value === "string" && value.trim() ? value : fallback;

const getStoredMessages = (sourceId: string): WorkspaceMessage[] => {
  if (typeof window === "undefined" || !sourceId) {
    return [];
  }

  try {
    const raw = window.localStorage.getItem(
      `${SPREADSHEET_CHAT_STORAGE_PREFIX}${sourceId}`,
    );
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? (parsed.filter(isRecord) as unknown as WorkspaceMessage[])
      : [];
  } catch {
    return [];
  }
};

const buildHistory = (messages: WorkspaceMessage[]): WorkspaceHistoryMessage[] => {
  const history: WorkspaceHistoryMessage[] = [];

  messages.slice(-8).forEach((message) => {
    history.push({ role: "user", content: message.user_prompt });
    if (message.ai_explanation?.trim()) {
      history.push({ role: "assistant", content: message.ai_explanation });
    }
  });

  return history;
};

const getAnalysisList = (
  source: SpreadsheetSource | undefined,
  key: string,
): Record<string, unknown>[] => {
  const value = source?.analysis_metadata?.[key];
  return Array.isArray(value) ? value.filter(isRecord) : [];
};

const getSuggestedQuestions = (source: SpreadsheetSource | undefined): string[] => {
  const items = source?.analysis_metadata?.suggested_questions;
  return Array.isArray(items)
    ? items.filter((item): item is string => typeof item === "string")
    : [];
};

const SpreadsheetChatPage: React.FC = () => {
  const { sourceId = "" } = useParams<{ sourceId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState<WorkspaceMessage[]>(() =>
    getStoredMessages(sourceId),
  );
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);
  const [transientError, setTransientError] = useState<string | null>(null);
  const [showTypingIndicator, setShowTypingIndicator] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const typingStartedAtRef = useRef<number | null>(null);

  const { data: sourceList } = useQuery<ConnectedSourceList>({
    queryKey: ["connected-sources"],
    queryFn: async () => {
      const res = await api.get("/connections/sources");
      return res.data as ConnectedSourceList;
    },
  });

  const { data: spreadsheet, isLoading } = useQuery<SpreadsheetSource>({
    queryKey: ["spreadsheet-source", sourceId],
    enabled: Boolean(sourceId),
    queryFn: async () => {
      const res = await api.get(`/connections/spreadsheets/${sourceId}`);
      return res.data as SpreadsheetSource;
    },
  });

  const sources = sourceList?.items ?? [];
  const currentSource = sources.find((source) => source.id === sourceId);
  const rawTables = spreadsheet?.dataset_payload?.tables;
  const spreadsheetTables = Array.isArray(rawTables)
    ? rawTables.filter(isRecord)
    : [];
  const tableCount = spreadsheetTables.length;
  const rowCount = spreadsheetTables.reduce(
    (sum, table) => sum + (Number(table.row_count) || 0),
    0,
  );
  const columnCount = spreadsheetTables.reduce(
    (sum, table) =>
      sum + (Array.isArray(table.columns) ? table.columns.length : 0),
    0,
  );
  const quickInsights = getAnalysisList(spreadsheet, "insights").slice(0, 3);
  const qualityChecks = getAnalysisList(spreadsheet, "quality_checks").slice(0, 2);
  const suggestedQuestions = getSuggestedQuestions(spreadsheet).slice(0, 6);

  const createDatabaseSessionMutation = useMutation<ChatSession, Error, ConnectedSource>({
    mutationFn: async (source) => {
      const res = await api.post("/chat/sessions", {
        connection_id: source.id,
      });
      return res.data as ChatSession;
    },
    onSuccess: (session, source) => {
      setActiveConnectionId(source.id);
      navigate(`/chat/${session.id}`);
    },
  });

  useEffect(() => {
    setMessages(getStoredMessages(sourceId));
    setActiveConnectionId(sourceId || null);
  }, [sourceId]);

  useEffect(() => {
    document.title = `${spreadsheet?.name ?? "Spreadsheet Chat"} | Vayent`;
  }, [spreadsheet?.name]);

  useEffect(() => {
    if (typeof window !== "undefined" && sourceId) {
      window.localStorage.setItem(
        `${SPREADSHEET_CHAT_STORAGE_PREFIX}${sourceId}`,
        JSON.stringify(messages.slice(-30)),
      );
    }
  }, [messages, sourceId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pendingPrompt, transientError, showTypingIndicator]);

  const sendMessageMutation = useMutation<WorkspaceMessage, Error, string>({
    mutationFn: async (userPrompt) => {
      const res = await api.post("/chat/workspace/message", {
        user_prompt: userPrompt,
        source_ids: [sourceId],
        active_source_id: sourceId,
        connection_ids: [],
        active_connection_id: sourceId,
        history: buildHistory(messages),
      });
      return res.data as WorkspaceMessage;
    },
    onSuccess: (message) => {
      const elapsed =
        typingStartedAtRef.current === null
          ? CHAT_TYPING_MIN_DURATION_MS
          : Date.now() - typingStartedAtRef.current;
      const delay = Math.max(0, CHAT_TYPING_MIN_DURATION_MS - elapsed);

      setPendingPrompt(null);
      setTransientError(null);
      setMessages((current) => [...current, message]);
      window.setTimeout(() => setShowTypingIndicator(false), delay);
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["auth", "me"] }),
        queryClient.invalidateQueries({ queryKey: ["spreadsheet-source", sourceId] }),
      ]).catch(() => undefined);
    },
    onError: (error) => {
      setPendingPrompt(null);
      setTransientError(error.message);
      setShowTypingIndicator(false);
    },
  });

  const sendPrompt = (value = prompt) => {
    const trimmedPrompt = value.trim();
    if (!trimmedPrompt || !sourceId || sendMessageMutation.isPending) {
      return;
    }

    typingStartedAtRef.current = Date.now();
    setShowTypingIndicator(true);
    setPendingPrompt(trimmedPrompt);
    setTransientError(null);
    sendMessageMutation.mutate(trimmedPrompt);
    setPrompt("");
  };

  if (isLoading) {
    return (
      <div className="app-page">
        <div className="app-empty chat-empty">
          <p>Loading spreadsheet chat...</p>
        </div>
      </div>
    );
  }

  if (!spreadsheet) {
    return (
      <div className="app-page">
        <div className="app-empty chat-empty">
          <p>Spreadsheet source could not be loaded.</p>
          <Link to="/chat" className="brand-btn-primary">
            Choose a source
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="app-page chat-page-layout spreadsheet-chat-layout">
      <section className="app-panel spreadsheet-chat-sourcebar">
        <div className="spreadsheet-chat-source-main">
          <div>
            <p className="page-kicker">Spreadsheet Chat</p>
            <h1 className="chat-source-title">{spreadsheet.name}</h1>
          </div>

          <div className="spreadsheet-chat-source-select">
            <label className="page-kicker" htmlFor="spreadsheet-active-source">
              Active Source
            </label>
            <div className="glass-select-wrap">
              <select
                id="spreadsheet-active-source"
                className="input glass-select"
                value={sourceId}
                onChange={(event) => {
                  const nextSource = sources.find(
                    (source) => source.id === event.target.value,
                  );
                  if (!nextSource) {
                    return;
                  }
                  if (nextSource.source_type === "spreadsheet") {
                    navigate(`/chat/source/${nextSource.id}`);
                    return;
                  }
                  createDatabaseSessionMutation.mutate(nextSource);
                }}
              >
                {sources.map((source) => (
                  <option key={source.id} value={source.id}>
                    {source.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        <div className="spreadsheet-chat-stats">
          <span>{rowCount.toLocaleString()} rows</span>
          <span>{columnCount.toLocaleString()} fields</span>
          <span>{tableCount.toLocaleString()} sheet{tableCount === 1 ? "" : "s"}</span>
          <span>{currentSource?.source_kind ?? spreadsheet.source_kind}</span>
        </div>
      </section>

      <div className="spreadsheet-chat-grid">
        <aside className="app-panel spreadsheet-chat-context">
          <div>
            <p className="page-kicker">Profile</p>
            <h2 className="chat-context-title">Instant summary</h2>
          </div>

          <div className="spreadsheet-chat-context-list app-scroll-panel">
            {quickInsights.length > 0 ? (
              quickInsights.map((insight) => (
                <article key={`${insight.title}-${insight.body}`}>
                  <strong title={asText(insight.title, "Insight")}>
                    {asText(insight.title, "Insight")}
                  </strong>
                  <p title={asText(insight.body)}>{asText(insight.body)}</p>
                </article>
              ))
            ) : (
              <article>
                <strong>Ready for questions</strong>
                <p>Ask for counts, rows, categories, latest records, or anomalies.</p>
              </article>
            )}

            {qualityChecks.map((check) => (
              <article key={asText(check.table, "quality")}>
                <strong>{asText(check.table, "Quality check")}</strong>
                <p>
                  {Number(check.duplicate_records) || 0} duplicates;{" "}
                  {Array.isArray(check.missing_values)
                    ? check.missing_values.length
                    : 0}{" "}
                  fields with missing values.
                </p>
              </article>
            ))}
          </div>

          <div className="spreadsheet-chat-suggestions">
            <p className="page-kicker">Suggested Questions</p>
            {suggestedQuestions.map((question) => (
              <button
                key={question}
                type="button"
                onClick={() => sendPrompt(question)}
                disabled={sendMessageMutation.isPending}
              >
                {question}
              </button>
            ))}
          </div>
        </aside>

        <main className="chat-main-column spreadsheet-chat-main">
          <section className="app-panel chat-container">
            <div className="chat-messages app-scroll-panel">
              {messages.length === 0 && !pendingPrompt ? (
                <div className="app-empty chat-empty">
                  <p>Ask about this spreadsheet</p>
                  <p>
                    Try counts, lists, recent registrations, category breakdowns,
                    duplicate checks, or summary insights.
                  </p>
                </div>
              ) : null}

              {messages.map((message) => (
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
                      <span>Vayent</span>
                      <span
                        className={`chat-status ${
                          message.execution_status === "error"
                            ? "chat-status-error"
                            : message.execution_status === "executed"
                              ? "chat-status-success"
                              : "chat-status-pending"
                        }`}
                      >
                        {message.execution_status}
                      </span>
                    </div>

                    <p className="chat-assistant-copy">
                      {message.ai_explanation || "No response was generated."}
                    </p>

                    {message.query_results.length > 0 ? (
                      <details className="chat-details">
                        <summary>Evidence</summary>
                        {message.query_results.map((result) => (
                          <div
                            key={`${message.id}-${result.connection_id}`}
                            className="chat-block"
                          >
                            <p className="page-kicker">
                              {result.row_count} row
                              {result.row_count === 1 ? "" : "s"}
                              {result.truncated ? " - preview" : ""}
                            </p>
                            <pre className="code-block app-scroll-x">
                              {JSON.stringify(result.rows.slice(0, 20), null, 2)}
                            </pre>
                          </div>
                        ))}
                      </details>
                    ) : null}
                  </article>
                </React.Fragment>
              ))}

              {pendingPrompt ? (
                <article className="chat-bubble chat-bubble-user">
                  <div className="chat-bubble-head">
                    <span>You</span>
                    <span>sending</span>
                  </div>
                  <p>{pendingPrompt}</p>
                </article>
              ) : null}

              {showTypingIndicator ? (
                <article className="chat-bubble chat-bubble-assistant chat-bubble-typing">
                  <div className="chat-bubble-head">
                    <span>Vayent</span>
                    <span className="chat-status chat-status-pending">thinking</span>
                  </div>
                  <div className="chat-typing-row">
                    <span className="chat-typing-label">Reading spreadsheet rows</span>
                    <div className="chat-typing-dots" aria-hidden="true">
                      <span />
                      <span />
                      <span />
                    </div>
                  </div>
                </article>
              ) : null}

              {transientError ? (
                <article className="chat-bubble chat-bubble-assistant chat-bubble-error">
                  <div className="chat-bubble-head">
                    <span>Vayent</span>
                    <span className="chat-status chat-status-error">error</span>
                  </div>
                  <p className="chat-assistant-copy">{transientError}</p>
                </article>
              ) : null}

              <div ref={bottomRef} />
            </div>
          </section>

          <section className="app-panel chat-composer">
            <div className="chat-input-row">
              <input
                className="input chat-composer-input"
                placeholder="Ask about teams, rows, status counts, latest registrations, or anomalies..."
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    sendPrompt();
                  }
                }}
                disabled={sendMessageMutation.isPending}
              />
              <button
                type="button"
                className="brand-btn-primary"
                onClick={() => sendPrompt()}
                disabled={sendMessageMutation.isPending || !prompt.trim()}
              >
                {sendMessageMutation.isPending ? "Sending..." : "Send"}
              </button>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
};

export default SpreadsheetChatPage;
