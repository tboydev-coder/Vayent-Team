import React from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";

import api from "../services/api";
import { setActiveConnectionId } from "../utils/activeConnection";
import "../styles/chatStartPage.css";
import type { ChatSession, ConnectedSource, ConnectedSourceList } from "../types";

interface OpenSourceResult {
  source: ConnectedSource;
  session?: ChatSession;
}

const ChatStartPage: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const preselectedSourceId =
    searchParams.get("sourceId") ?? searchParams.get("connectionId");

  const { data: sourceList, isLoading } = useQuery<ConnectedSourceList>({
    queryKey: ["connected-sources"],
    queryFn: async () => {
      const res = await api.get("/connections/sources");
      return res.data as ConnectedSourceList;
    },
  });

  const sources = sourceList?.items ?? [];
  const filteredSources = preselectedSourceId
    ? (() => {
        const match = sources.find((source) => source.id === preselectedSourceId);
        return match ? [match] : sources;
      })()
    : sources;

  const openSourceMutation = useMutation<OpenSourceResult, Error, ConnectedSource>({
    mutationFn: async (source) => {
      if (source.source_type === "spreadsheet") {
        return { source };
      }
      const res = await api.post("/chat/sessions", {
        connection_id: source.id,
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
  });

  const openingSourceId =
    openSourceMutation.isPending && openSourceMutation.variables
      ? openSourceMutation.variables.id
      : null;

  return (
    <div className="app-page chat-start-layout">
      <section className="dashboard-hero">
        <div className="chat-start-hero">
          <div className="chat-start-copy">
            <p className="page-kicker">AI Session Hub</p>
            <h1 className="display-title">Launch a new data conversation.</h1>
            <p className="page-text">
              Pick a source and Vayent will open a plain-language workspace for
              insight, planning, visualization, and schema-aware follow-up.
            </p>
          </div>

          <div className="brand-pill">
            {filteredSources.length} available sources
          </div>
        </div>
      </section>

      {isLoading ? (
        <div className="app-empty chat-start-empty">
          <p>Loading connections...</p>
        </div>
      ) : filteredSources.length === 0 ? (
        <div className="app-empty chat-start-empty">
          <p>You need a source before opening chat.</p>
          <p>Add a database or spreadsheet, then come back to launch an AI session.</p>
          <Link to="/connections" className="brand-btn-primary">
            Add a source
          </Link>
        </div>
      ) : (
        <div className="chat-start-grid">
          {filteredSources.map((source) => {
            const isOpening = openingSourceId === source.id;
            const kindLabel =
              source.source_type === "database"
                ? source.source_kind
                : source.source_kind === "link"
                  ? "Excel link"
                  : "Spreadsheet";

            return (
              <button
                key={source.id}
                type="button"
                onClick={() => {
                  if (openSourceMutation.isPending) {
                    return;
                  }

                  openSourceMutation.mutate(source);
                }}
                className={`chat-start-card ${
                  isOpening ? "chat-start-card-opening" : ""
                }`}
                disabled={isOpening}
                aria-busy={isOpening}
              >
                <div className="chat-start-card-head">
                  <span className="brand-badge">{kindLabel}</span>
                  <span className="chat-start-card-kicker">
                    {source.source_type === "database" ? "Database" : "Spreadsheet"}
                  </span>
                </div>

                <div>
                  <p className="chat-start-card-title">{source.name}</p>
                  <p className="chat-start-card-copy">{source.detail}</p>
                </div>

                <div className="chat-start-card-foot">
                  <span className="chat-start-card-action">
                    {isOpening
                      ? "Opening..."
                      : source.source_type === "spreadsheet"
                        ? "Open chat"
                        : "Open chat"}
                  </span>
                  <span className="chat-start-card-tag">
                    {isOpening ? "Live" : source.source_type}
                  </span>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default ChatStartPage;
