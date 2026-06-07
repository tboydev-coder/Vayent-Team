import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "../services/api";
import { useAuthStore } from "../store/auth";
import { setActiveConnectionId } from "../utils/activeConnection";
import "../styles/chatPage.css";
import type {
  ChatMessage,
  ChatSession,
  ChatSessionSummary,
  ExecuteQueryResponse,
} from "../types";

interface PendingConfirmation {
  confirmationToken: string;
  messageId: string;
  sql: string;
}

const CHAT_HISTORY_WIDTH_KEY = "vayent_chat_history_width";
const CHAT_TYPING_MIN_DURATION_MS = 2000;
const CHAT_THINKING_MESSAGES = [
  "Vayent is thinking",
  "Reading the schema context",
  "Checking the safest path",
  "Preparing a plain-language answer",
];

const ChatPage: React.FC = () => {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const currentUser = useAuthStore((state) => state.user);
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [historyWidth, setHistoryWidth] = useState(() => {
    if (typeof window === "undefined") {
      return 340;
    }

    const stored = window.localStorage.getItem(CHAT_HISTORY_WIDTH_KEY);
    const parsed = stored ? Number(stored) : NaN;
    return Number.isFinite(parsed) ? parsed : 340;
  });
  const [isResizingHistory, setIsResizingHistory] = useState(false);
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);
  const [showTypingIndicator, setShowTypingIndicator] = useState(false);
  const [thinkingMessageIndex, setThinkingMessageIndex] = useState(0);
  const [transientAssistantError, setTransientAssistantError] = useState<
    string | null
  >(null);
  const [pendingConfirmation, setPendingConfirmation] =
    useState<PendingConfirmation | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const typingStartedAtRef = useRef<number | null>(null);
  const typingDismissTimeoutRef = useRef<number | null>(null);

  const { data: session, isLoading } = useQuery<ChatSession>({
    queryKey: ["chat-session", sessionId],
    enabled: Boolean(sessionId),
    queryFn: async () => {
      const res = await api.get(`/chat/sessions/${sessionId}`);
      return res.data as ChatSession;
    },
  });

  const { data: sessionHistory = [] } = useQuery<ChatSessionSummary[]>({
    queryKey: ["chat-sessions", session?.connection_id ?? "pending"],
    enabled: Boolean(session?.connection_id),
    queryFn: async () => {
      const res = await api.get("/chat/sessions", {
        params: { connection_id: session?.connection_id },
      });
      return res.data as ChatSessionSummary[];
    },
  });

  const sessionTitle = useMemo(() => {
    if (!session) {
      return "Chat with Database";
    }

    return (
      session.title?.trim() ||
      session.connection_name?.trim() ||
      "Chat with Database"
    );
  }, [session]);

  const tokenStatusLabel =
    currentUser?.remaining_tokens === null ||
    currentUser?.remaining_tokens === undefined
      ? `${currentUser?.daily_token_usage?.toLocaleString() ?? "0"} tokens used today`
      : `${currentUser.remaining_tokens.toLocaleString()} tokens left today`;

  useEffect(() => {
    document.title = `${sessionTitle} | Vayent`;
  }, [sessionTitle]);

  useEffect(() => {
    if (session?.connection_id) {
      setActiveConnectionId(session.connection_id);
    }
  }, [session?.connection_id]);

  useEffect(() => {
    window.localStorage.setItem(CHAT_HISTORY_WIDTH_KEY, String(historyWidth));
  }, [historyWidth]);

  useEffect(() => {
    if (!isResizingHistory) {
      return undefined;
    }

    const handlePointerMove = (event: PointerEvent) => {
      if (!shellRef.current) {
        return;
      }

      const bounds = shellRef.current.getBoundingClientRect();
      const nextWidth = Math.min(
        520,
        Math.max(260, event.clientX - bounds.left),
      );
      setHistoryWidth(nextWidth);
    };

    const stopResizing = () => {
      setIsResizingHistory(false);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    };

    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopResizing);

    return () => {
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopResizing);
    };
  }, [isResizingHistory]);

  const clearTypingDismissTimeout = () => {
    if (typingDismissTimeoutRef.current === null) {
      return;
    }

    window.clearTimeout(typingDismissTimeoutRef.current);
    typingDismissTimeoutRef.current = null;
  };

  const hideTypingIndicator = () => {
    clearTypingDismissTimeout();
    typingStartedAtRef.current = null;
    setShowTypingIndicator(false);
  };

  const scheduleTypingIndicatorDismiss = () => {
    if (typingStartedAtRef.current === null) {
      hideTypingIndicator();
      return;
    }

    const elapsed = Date.now() - typingStartedAtRef.current;
    const remaining = Math.max(0, CHAT_TYPING_MIN_DURATION_MS - elapsed);

    clearTypingDismissTimeout();

    if (remaining === 0) {
      hideTypingIndicator();
      return;
    }

    typingDismissTimeoutRef.current = window.setTimeout(() => {
      typingDismissTimeoutRef.current = null;
      typingStartedAtRef.current = null;
      setShowTypingIndicator(false);
    }, remaining);
  };

  useEffect(() => {
    return () => {
      clearTypingDismissTimeout();
      typingStartedAtRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!showTypingIndicator) {
      setThinkingMessageIndex(0);
      return undefined;
    }

    const interval = window.setInterval(() => {
      setThinkingMessageIndex(
        (current) => (current + 1) % CHAT_THINKING_MESSAGES.length,
      );
    }, 1800);

    return () => window.clearInterval(interval);
  }, [showTypingIndicator]);

  const sendMessageMutation = useMutation<ChatMessage, Error, string>({
    mutationFn: async (userPrompt) => {
      const res = await api.post(`/chat/sessions/${sessionId}/messages`, {
        session_id: sessionId,
        user_prompt: userPrompt,
      });
      return res.data as ChatMessage;
    },
    onSuccess: (message) => {
      setPendingPrompt(null);
      setTransientAssistantError(null);
      setMessages((current) => [...current, message]);
      scheduleTypingIndicatorDismiss();

      if (
        message.requires_confirmation &&
        message.confirmation_token &&
        message.generated_sql
      ) {
        setPendingConfirmation({
          confirmationToken: message.confirmation_token,
          messageId: message.id,
          sql: message.generated_sql,
        });
      }

      void Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["chat-session", sessionId],
        }),
        queryClient.invalidateQueries({ queryKey: ["logs"] }),
        queryClient.invalidateQueries({ queryKey: ["recent-queries"] }),
        queryClient.invalidateQueries({ queryKey: ["chat-sessions"] }),
        queryClient.invalidateQueries({ queryKey: ["auth", "me"] }),
      ]).catch((refreshError) => {
        console.error(
          "Failed to refresh chat state after sending.",
          refreshError,
        );
      });
    },
    onError: (error) => {
      setPendingPrompt(null);
      setTransientAssistantError(error.message);
      hideTypingIndicator();
    },
  });

  const sendMessagePending = sendMessageMutation.isPending;

  useEffect(() => {
    if (!session?.messages) {
      return;
    }

    // Merge server messages with any locally-appended message so technical fields
    // (generated_sql/query_result) never "blink out" on refetch.
    setMessages((current) => {
      const localById = new Map(
        current.map((message) => [message.id, message]),
      );
      const merged = session.messages.map((serverMessage) => {
        const local = localById.get(serverMessage.id);
        if (!local) {
          return serverMessage;
        }

        return {
          ...serverMessage,
          generated_sql:
            serverMessage.generated_sql ?? local.generated_sql ?? null,
          query_result:
            serverMessage.query_result ?? local.query_result ?? null,
          ai_explanation:
            serverMessage.ai_explanation ?? local.ai_explanation ?? null,
        };
      });

      return [...merged].sort(
        (left, right) =>
          new Date(left.created_at).getTime() -
          new Date(right.created_at).getTime(),
      );
    });
  }, [session]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pendingPrompt, showTypingIndicator, transientAssistantError]);

  const confirmMutation = useMutation<ExecuteQueryResponse, Error>({
    mutationFn: async () => {
      if (!pendingConfirmation || !session) {
        throw new Error("Confirmation details are missing.");
      }

      const res = await api.post("/chat/confirm-query", {
        confirmation_token: pendingConfirmation.confirmationToken,
        connection_id: session.connection_id,
        message_id: pendingConfirmation.messageId,
      });
      return res.data as ExecuteQueryResponse;
    },
    onSuccess: () => {
      setPendingConfirmation(null);
      void Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["chat-session", sessionId],
        }),
        queryClient.invalidateQueries({ queryKey: ["chat-sessions"] }),
        queryClient.invalidateQueries({ queryKey: ["logs"] }),
        queryClient.invalidateQueries({ queryKey: ["recent-queries"] }),
      ]).catch((refreshError) => {
        console.error(
          "Failed to refresh chat state after confirmation.",
          refreshError,
        );
      });
    },
    onError: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["chat-session", sessionId],
      });
    },
  });

  const createSessionMutation = useMutation<ChatSession, Error>({
    mutationFn: async () => {
      if (!session) {
        throw new Error("The current chat session is not loaded.");
      }

      const res = await api.post("/chat/sessions", {
        connection_id: session.connection_id,
      });
      return res.data as ChatSession;
    },
    onSuccess: (nextSession) => {
      navigate(`/chat/${nextSession.id}`);
    },
  });

  const sendPrompt = () => {
    const trimmedPrompt = prompt.trim();
    if (!trimmedPrompt || !sessionId) {
      return;
    }

    clearTypingDismissTimeout();
    typingStartedAtRef.current = Date.now();
    setThinkingMessageIndex(0);
    setShowTypingIndicator(true);
    setPendingPrompt(trimmedPrompt);
    setTransientAssistantError(null);
    sendMessageMutation.mutate(trimmedPrompt);
    setPrompt("");
  };

  if (isLoading) {
    return (
      <div className="app-page">
        <div className="app-empty chat-empty">
          <p>Loading chat session...</p>
        </div>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="app-page">
        <div className="app-empty chat-empty">
          <p>Chat session could not be loaded.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="app-page chat-page-layout">
      <section className="dashboard-hero chat-header">
        <div className="chat-header-row">
          <div className="chat-header-copy">
            <p className="page-kicker">AI Conversation</p>
            <h1 className="display-title">{sessionTitle}</h1>
            <p className="page-text">
              Ask naturally. Vayent translates technical data work into usable
              insight, planning guidance, visual context, and schema-aware
              answers without making you think in SQL.
            </p>
          </div>

          <div className="chat-header-meta">
            <div className="brand-pill">
              {session.connection_name?.trim() || "Connected database"}
            </div>
            <div className="brand-pill">{messages.length} turns stored</div>
            <div className="brand-pill">{tokenStatusLabel}</div>
          </div>
        </div>
      </section>

      <div
        ref={shellRef}
        className="chat-shell"
        style={{ ["--chat-history-width" as string]: `${historyWidth}px` }}
      >
        <aside className="app-panel chat-history-panel">
          <div className="chat-history-head">
            <div>
              <p className="page-kicker">History</p>
              <h2 className="chat-history-title">
                {session.connection_name?.trim() || "This database"}
              </h2>
            </div>
            <button
              type="button"
              className="brand-btn-secondary"
              onClick={() => createSessionMutation.mutate()}
              disabled={createSessionMutation.isPending}
            >
              {createSessionMutation.isPending ? "Creating..." : "New thread"}
            </button>
          </div>

          <div className="chat-history-list app-scroll-panel">
            {sessionHistory.length === 0 ? (
              <div className="chat-history-list-empty">
                Titles appear here after this database has at least one saved
                reply.
              </div>
            ) : (
              sessionHistory.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`chat-history-item ${
                    item.id === session.id ? "chat-history-item-active" : ""
                  }`}
                  onClick={() => navigate(`/chat/${item.id}`)}
                >
                  <p className="chat-history-item-title">
                    {item.title?.trim() || "Untitled chat"}
                  </p>
                </button>
              ))
            )}
          </div>
        </aside>

        <div
          className={`chat-divider ${isResizingHistory ? "chat-divider-active" : ""}`}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize conversation history"
          onPointerDown={() => setIsResizingHistory(true)}
        />

        <div className="chat-main-column">
          <section className="app-panel chat-container">
            <div className="chat-messages app-scroll-panel">
              {messages.length === 0 && !pendingPrompt ? (
                <div className="app-empty chat-empty">
                  <p>Start the conversation</p>
                  <p>
                    Ask about a table, a KPI, product direction, data quality,
                    growth opportunities, or a follow-up business question
                    without repeating the full context.
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
                        message.execution_status === "error"
                          ? "chat-bubble-error"
                          : ""
                      }`}
                    >
                      <div className="chat-bubble-head">
                        <span>Vayent</span>
                        <span className={statusClass}>
                          {message.execution_status}
                        </span>
                      </div>

                      <p className="chat-assistant-copy">
                        {message.ai_explanation || "No response was generated."}
                      </p>

                      {message.requires_confirmation &&
                      message.confirmation_token ? (
                        <button
                          type="button"
                          className="brand-btn-secondary"
                          onClick={() =>
                            setPendingConfirmation({
                              confirmationToken:
                                message.confirmation_token as string,
                              messageId: message.id,
                              sql: message.generated_sql || "",
                            })
                          }
                        >
                          Review pending query
                        </button>
                      ) : null}

                      {message.generated_sql || message.query_result ? (
                        <details className="chat-details">
                          <summary>Technical details</summary>

                          {message.generated_sql ? (
                            <div className="chat-block">
                              <p className="page-kicker">SQL</p>
                              <pre className="code-block app-scroll-x">
                                {message.generated_sql}
                              </pre>
                            </div>
                          ) : null}

                          {message.query_result ? (
                            <div className="chat-block">
                              <p className="page-kicker">Result</p>
                              <pre className="code-block app-scroll-x">
                                {JSON.stringify(message.query_result, null, 2)}
                              </pre>
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

              {showTypingIndicator ? (
                <article className="chat-bubble chat-bubble-assistant chat-bubble-typing">
                  <div className="chat-bubble-head">
                    <span>Vayent</span>
                    <span className="chat-status chat-status-pending">
                      thinking
                    </span>
                  </div>
                  <div className="chat-typing-row">
                    <span className="chat-typing-label">
                      {CHAT_THINKING_MESSAGES[thinkingMessageIndex]}
                    </span>
                    <div className="chat-typing-dots" aria-hidden="true">
                      <span />
                      <span />
                      <span />
                    </div>
                  </div>
                </article>
              ) : null}

              {transientAssistantError ? (
                <article className="chat-bubble chat-bubble-assistant chat-bubble-error">
                  <div className="chat-bubble-head">
                    <span>Vayent</span>
                    <span className="chat-status chat-status-error">error</span>
                  </div>
                  <p className="chat-assistant-copy">
                    {transientAssistantError}
                  </p>
                </article>
              ) : null}

              <div ref={bottomRef} />
            </div>
          </section>

          <section className="app-panel chat-composer">
            <div className="chat-composer-head">
              <p className="page-kicker">Compose</p>
              <p className="chat-composer-note">
                Vayent adapts each answer to the question, hides technical names
                when aliases exist, and uses your available tokens for AI work.
              </p>
            </div>

            <div className="chat-input-row">
              <input
                className="input chat-composer-input"
                placeholder="Ask about this database, your business, or the app..."
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    sendPrompt();
                  }
                }}
                disabled={sendMessagePending}
              />

              <button
                type="button"
                className="brand-btn-primary"
                onClick={sendPrompt}
                disabled={sendMessagePending}
              >
                {sendMessagePending ? "Sending..." : "Send"}
              </button>
            </div>
          </section>
        </div>
      </div>

      {pendingConfirmation ? (
        <div className="chat-modal-overlay">
          <div className="app-panel-strong chat-modal">
            <p className="page-kicker">Confirm query</p>
            <h3>Review SQL before execution</h3>
            <p className="chat-modal-copy">
              Vayent flagged this request as a write operation. Confirm it only
              if you expect data to be changed.
            </p>
            <pre className="code-block app-scroll-x">
              {pendingConfirmation.sql}
            </pre>

            {confirmMutation.error ? (
              <div className="chat-error">{confirmMutation.error.message}</div>
            ) : null}

            <div className="chat-modal-actions">
              <button
                type="button"
                className="brand-btn-primary"
                onClick={() => confirmMutation.mutate()}
                disabled={confirmMutation.isPending}
              >
                {confirmMutation.isPending ? "Running..." : "Confirm"}
              </button>

              <button
                type="button"
                className="brand-btn-secondary"
                onClick={() => setPendingConfirmation(null)}
                disabled={confirmMutation.isPending}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default ChatPage;
