import React, { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import api from "../services/api";
import "../styles/voiceConversation.css";
import type { ConnectedSource, ConnectedSourceList } from "../types";

type VoiceStatus =
  | "not-connected"
  | "connecting"
  | "live"
  | "muted"
  | "ended"
  | "error";

interface VoiceCapabilities {
  supports_voice: boolean;
  supported_sources: string[];
  aethex_configured: boolean;
  conversation_mode: string;
  transport_modes: string[];
  live_tool_name?: string;
}

interface VoiceSourceSession {
  session_id: string;
  source_id: string;
  source_type: "database" | "spreadsheet";
  source_name: string;
  connection_name?: string;
  schema_kind?: string;
  row_count?: number;
  table_count?: number;
  greeting?: string;
  source_overview?: string;
  source_metadata?: Record<string, unknown>;
  live_tool_name?: string;
}

interface VoiceRealtimeEvent {
  type: string;
  session?: Record<string, unknown>;
  response?: Record<string, unknown>;
}

interface AethexSessionPayload {
  session_id?: string;
  sessionId?: string;
  id?: string;
  ice_config?: {
    iceServers?: RTCIceServer[];
  };
  iceServers?: RTCIceServer[];
  ice?: RTCIceServer[];
  vayent_context?: {
    source_id: string;
    source_name: string;
    connection_name: string;
    source_type: "database" | "spreadsheet";
    schema_kind?: string;
    local_session_id: string;
    greeting?: string;
    source_overview?: string;
    source_metadata?: Record<string, unknown>;
    live_tool_name: string;
    bootstrap_events?: VoiceRealtimeEvent[];
    tool_instruction: VoiceRealtimeEvent;
  };
}

interface VoiceToolBridgeResponse {
  success: boolean;
  tool_name: string;
  call_id?: string | null;
  output_text?: string | null;
  grounding?: Record<string, unknown>;
  result?: Record<string, unknown>;
}

interface ParsedToolCall {
  callId?: string;
  question: string;
  toolName: string;
}

const ICE_GATHERING_TIMEOUT_MS = 300000;
const DISCONNECTED_GRACE_PERIOD_MS = 15000;

const waitForIceGatheringComplete = async (
  peerConnection: RTCPeerConnection,
  timeoutMs = ICE_GATHERING_TIMEOUT_MS,
) => {
  if (peerConnection.iceGatheringState === "complete") {
    return;
  }

  await new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      peerConnection.onicegatheringstatechange = null;
      reject(
        new Error(
          `ICE gathering timed out after ${Math.round(timeoutMs / 1000)} seconds. ` +
            "This usually points to a slow or restricted network path rather than the selected data source.",
        ),
      );
    }, timeoutMs);

    peerConnection.onicegatheringstatechange = () => {
      if (peerConnection.iceGatheringState === "complete") {
        window.clearTimeout(timeout);
        peerConnection.onicegatheringstatechange = null;
        resolve();
      }
    };
  });
};

const LIVE_PROMPTS = [
  "What were my highest sales last month?",
  "Show me customers from Lagos.",
  "How many failed transactions do I have?",
  "Which products generated the most revenue?",
  "Show the records behind that.",
  "Compare January versus February.",
];

const sourceKindLabel = (source: ConnectedSource) =>
  source.source_type === "database"
    ? source.source_kind
    : source.source_kind === "link"
      ? "spreadsheet link"
      : source.metadata?.file_type
        ? String(source.metadata.file_type)
        : "spreadsheet";

const parseToolCall = (
  payload: unknown,
  expectedToolName: string,
): ParsedToolCall | null => {
  if (!payload || typeof payload !== "object") {
    return null;
  }

  const event = payload as Record<string, unknown>;
  const toolName =
    (typeof event.name === "string" && event.name) ||
    (typeof event.tool_name === "string" && event.tool_name) ||
    (typeof event.function_name === "string" && event.function_name) ||
    "";

  if (toolName !== expectedToolName) {
    return null;
  }

  const callId =
    (typeof event.call_id === "string" && event.call_id) ||
    (typeof event.id === "string" && event.id) ||
    undefined;

  const rawArguments =
    event.arguments ||
    event.args ||
    (typeof event.payload === "object" ? event.payload : undefined);

  const collectQuestion = (candidate: unknown): string | null => {
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
    if (candidate && typeof candidate === "object") {
      const record = candidate as Record<string, unknown>;
      for (const key of [
        "question",
        "text",
        "user_question",
        "query",
        "prompt",
      ]) {
        const value = record[key];
        if (typeof value === "string" && value.trim()) {
          return value.trim();
        }
      }
    }
    return null;
  };

  let question =
    collectQuestion(event) || collectQuestion(rawArguments) || null;

  if (!question && typeof rawArguments === "string") {
    try {
      const parsed = JSON.parse(rawArguments) as Record<string, unknown>;
      question = collectQuestion(parsed);
    } catch {
      question = rawArguments.trim() || null;
    }
  }

  if (!question) {
    return null;
  }

  return { callId, question, toolName };
};

const VoiceConversationPage: React.FC = () => {
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [status, setStatus] = useState<VoiceStatus>("not-connected");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [session, setSession] = useState<VoiceSourceSession | null>(null);
  const [isMuted, setIsMuted] = useState(false);
  const [remoteToolActivity, setRemoteToolActivity] = useState<string | null>(
    null,
  );
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const remoteAudioRef = useRef<HTMLAudioElement | null>(null);
  const localStreamRef = useRef<MediaStream | null>(null);
  const remoteSessionIdRef = useRef<string | null>(null);
  const toolChannelRef = useRef<RTCDataChannel | null>(null);
  const liveToolNameRef = useRef("query_selected_source");
  const bootstrapSentRef = useRef(false);
  const disconnectTimerRef = useRef<number | null>(null);

  const { data: capabilities } = useQuery<VoiceCapabilities>({
    queryKey: ["voice-capabilities"],
    queryFn: async () => {
      const res = await api.get("/voice/capabilities");
      return res.data as VoiceCapabilities;
    },
  });

  const { data: sourceList, isLoading: isLoadingSources } =
    useQuery<ConnectedSourceList>({
      queryKey: ["connected-sources"],
      queryFn: async () => {
        const res = await api.get("/connections/sources");
        return res.data as ConnectedSourceList;
      },
    });

  const sources = useMemo(() => sourceList?.items ?? [], [sourceList?.items]);
  const selectedSource =
    sources.find((source) => source.id === selectedSourceId) ?? null;
  const sessionLocked =
    status === "connecting" || status === "live" || status === "muted";

  useEffect(() => {
    if (!selectedSourceId && sources.length > 0) {
      setSelectedSourceId(sources[0].id);
    }
  }, [selectedSourceId, sources]);

  useEffect(() => {
    if (capabilities?.live_tool_name) {
      liveToolNameRef.current = capabilities.live_tool_name;
    }
  }, [capabilities?.live_tool_name]);

  useEffect(() => {
    return () => {
      void teardownVoiceSession(false);
    };
  }, []);

  const clearDisconnectTimer = () => {
    if (disconnectTimerRef.current !== null) {
      window.clearTimeout(disconnectTimerRef.current);
      disconnectTimerRef.current = null;
    }
  };

  const currentStatusLabel =
    isMuted && (status === "live" || status === "muted") ? "muted" : status;

  const postToolResultToChannel = (
    channel: RTCDataChannel,
    bridgeResponse: VoiceToolBridgeResponse,
  ) => {
    const payloads = [
      {
        type: "tool_result",
        tool_name: bridgeResponse.tool_name,
        call_id: bridgeResponse.call_id,
        output_text: bridgeResponse.output_text,
        grounding: bridgeResponse.grounding,
      },
      {
        type: "function_call_output",
        name: bridgeResponse.tool_name,
        call_id: bridgeResponse.call_id,
        output: bridgeResponse.output_text,
        grounding: bridgeResponse.grounding,
      },
      {
        type: "conversation.item.create",
        item: {
          type: "function_call_output",
          call_id: bridgeResponse.call_id,
          name: bridgeResponse.tool_name,
          output: JSON.stringify({
            answer: bridgeResponse.output_text,
            grounding: bridgeResponse.grounding,
          }),
        },
      },
      {
        type: "response.create",
      },
    ];

    payloads.forEach((payload) => {
      try {
        channel.send(JSON.stringify(payload));
      } catch {
        // Best effort relay only.
      }
    });
  };

  const handleToolCallEvent = async (
    eventPayload: unknown,
    channel: RTCDataChannel,
  ) => {
    const parsedCall = parseToolCall(eventPayload, liveToolNameRef.current);
    if (!parsedCall || !remoteSessionIdRef.current) {
      return;
    }

    setRemoteToolActivity("Querying the selected source");

    try {
      const res = await api.post(
        `/voice/session/${remoteSessionIdRef.current}/tool/query-selected-source`,
        {
          question: parsedCall.question,
          call_id: parsedCall.callId,
          tool_name: parsedCall.toolName,
          event: eventPayload,
        },
      );
      postToolResultToChannel(channel, res.data as VoiceToolBridgeResponse);
      setRemoteToolActivity("Grounded answer returned to live agent");
    } catch (error) {
      setRemoteToolActivity("Tool bridge error");
      const message =
        error instanceof Error ? error.message : "Live tool bridge failed.";
      try {
        channel.send(
          JSON.stringify({
            type: "tool_error",
            tool_name: parsedCall.toolName,
            call_id: parsedCall.callId,
            error: message,
          }),
        );
      } catch {
        // Ignore relay failure.
      }
    }
  };

  const registerDataChannel = (
    channel: RTCDataChannel,
    bootstrapEvents?: VoiceRealtimeEvent[],
  ) => {
    toolChannelRef.current = channel;

    channel.onopen = () => {
      if (!bootstrapEvents?.length || bootstrapSentRef.current) {
        return;
      }
      bootstrapSentRef.current = true;
      bootstrapEvents.forEach((eventPayload) => {
        try {
          channel.send(JSON.stringify(eventPayload));
        } catch {
          // Ignore unsupported control messages.
        }
      });
    };

    channel.onmessage = (event) => {
      try {
        const payload = JSON.parse(String(event.data));
        void handleToolCallEvent(payload, channel);
      } catch {
        // Ignore non-JSON or unsupported channel messages.
      }
    };
  };

  const teardownVoiceSession = async (notifyRemote = true) => {
    clearDisconnectTimer();

    if (notifyRemote && remoteSessionIdRef.current) {
      try {
        await api.post(`/voice/session/${remoteSessionIdRef.current}/close`);
      } catch {
        // Best effort cleanup only.
      }
    }

    if (toolChannelRef.current) {
      try {
        toolChannelRef.current.close();
      } catch {
        // Ignore teardown errors.
      }
      toolChannelRef.current = null;
    }

    if (pcRef.current) {
      try {
        pcRef.current.getSenders().forEach((sender) => {
          sender.track?.stop();
        });
        pcRef.current.close();
      } catch {
        // Ignore teardown errors.
      }
      pcRef.current = null;
    }

    if (localStreamRef.current) {
      localStreamRef.current.getTracks().forEach((track) => track.stop());
      localStreamRef.current = null;
    }

    if (remoteAudioRef.current) {
      remoteAudioRef.current.srcObject = null;
    }

    remoteSessionIdRef.current = null;
    bootstrapSentRef.current = false;
    setRemoteToolActivity(null);
  };

  const connectVoiceMutation = useMutation<void, Error>({
    mutationFn: async () => {
      if (!selectedSourceId) {
        throw new Error("Choose a source first.");
      }
      if (!capabilities?.aethex_configured) {
        throw new Error("Aethex is not configured on the server.");
      }

      setStatus("connecting");
      setErrorMessage(null);
      setRemoteToolActivity(null);

      const sourceRes = await api.post(
        `/voice/start-session?source_id=${selectedSourceId}`,
      );
      const sourceSession = sourceRes.data as VoiceSourceSession;
      setSession(sourceSession);

      const remoteRes = await api.post("/voice/session", {
        source_id: selectedSourceId,
        local_session_id: sourceSession.session_id,
      });
      const remotePayload = remoteRes.data as AethexSessionPayload;
      const remoteSessionId =
        remotePayload.session_id || remotePayload.sessionId || remotePayload.id;

      if (!remoteSessionId) {
        throw new Error("Aethex did not return a session ID.");
      }

      if (remotePayload.vayent_context?.live_tool_name) {
        liveToolNameRef.current = remotePayload.vayent_context.live_tool_name;
      }

      remoteSessionIdRef.current = remoteSessionId;
      const iceServers =
        remotePayload.iceServers ||
        remotePayload.ice_config?.iceServers ||
        remotePayload.ice ||
        [];

      const peerConnection = new RTCPeerConnection({ iceServers });
      pcRef.current = peerConnection;

      peerConnection.onconnectionstatechange = () => {
        const connectionState = peerConnection.connectionState;
        if (connectionState === "connected") {
          clearDisconnectTimer();
          setErrorMessage(null);
          setStatus(isMuted ? "muted" : "live");
        } else if (connectionState === "closed") {
          clearDisconnectTimer();
          setStatus("ended");
        } else if (connectionState === "failed") {
          clearDisconnectTimer();
          setStatus("error");
          setErrorMessage("The live voice session disconnected.");
        } else if (connectionState === "disconnected") {
          clearDisconnectTimer();
          disconnectTimerRef.current = window.setTimeout(() => {
            if (peerConnection.connectionState === "disconnected") {
              setStatus("error");
              setErrorMessage("The live voice session disconnected.");
            }
          }, DISCONNECTED_GRACE_PERIOD_MS);
        }
      };

      peerConnection.ontrack = (event) => {
        if (remoteAudioRef.current) {
          remoteAudioRef.current.srcObject = event.streams[0];
        }
      };

      peerConnection.ondatachannel = (event) => {
        registerDataChannel(
          event.channel,
          remotePayload.vayent_context?.bootstrap_events,
        );
      };

      const localToolChannel =
        peerConnection.createDataChannel("vayent-tool-bridge");
      registerDataChannel(
        localToolChannel,
        remotePayload.vayent_context?.bootstrap_events,
      );

      const localStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      localStreamRef.current = localStream;
      localStream.getTracks().forEach((track) => {
        track.enabled = !isMuted;
        peerConnection.addTrack(track, localStream);
      });

      const offer = await peerConnection.createOffer();
      await peerConnection.setLocalDescription(offer);
      await waitForIceGatheringComplete(peerConnection);

      const offerBody = {
        sdp: peerConnection.localDescription?.sdp,
        type: peerConnection.localDescription?.type,
      };
      const answerRes = await api.post(
        `/voice/session/${remoteSessionId}/offer`,
        offerBody,
      );
      const answer = answerRes.data ?? {};
      const sdp = answer.sdp || answer.answer?.sdp || answer.answer_sdp;
      const type = (answer.type || "answer") as RTCSdpType;

      if (!sdp) {
        throw new Error("Aethex did not return an SDP answer.");
      }

      await peerConnection.setRemoteDescription({ type, sdp });
    },
    onError: async (error) => {
      setStatus("error");
      setErrorMessage(error.message);
      await teardownVoiceSession(true);
    },
  });

  const endVoiceMutation = useMutation<void, Error>({
    mutationFn: async () => {
      await teardownVoiceSession(true);
      setSession(null);
      setIsMuted(false);
      setStatus("ended");
      setErrorMessage(null);
    },
    onError: async (error) => {
      setStatus("error");
      setErrorMessage(error.message);
      await teardownVoiceSession(false);
    },
  });

  const startVoiceMode = () => {
    void connectVoiceMutation.mutateAsync();
  };

  const endVoiceMode = () => {
    void endVoiceMutation.mutateAsync();
  };

  const toggleMute = () => {
    const nextMuted = !isMuted;
    setIsMuted(nextMuted);
    localStreamRef.current?.getAudioTracks().forEach((track) => {
      track.enabled = !nextMuted;
    });
    setStatus(nextMuted ? "muted" : "live");
  };

  const showLivePromptGuide =
    session !== null || status === "live" || status === "muted";

  return (
    <div className="app-page voice-page-layout">
      <audio ref={remoteAudioRef} autoPlay playsInline hidden />

      <section className="dashboard-hero voice-hero">
        <div className="voice-hero-copy">
          <p className="page-kicker">Voice Conversation</p>
          <h1 className="display-title">
            Hands-free voice mode for speaking directly with your connected
            data.
          </h1>
          <p className="page-text">
            Select a source, start the live Aethex session, and speak naturally.
            Source questions are relayed into Vayent&apos;s existing
            source-first retrieval pipeline through the live tool bridge.
          </p>
        </div>

        <div className="voice-hero-pills">
          <div className="brand-pill">{sources.length} connected sources</div>
          <div className="brand-pill">
            {capabilities?.aethex_configured ? "Aethex live" : "Aethex missing"}
          </div>
          <div className="brand-pill">
            {session ? session.source_name : "choose a source"}
          </div>
        </div>
      </section>

      <div className="voice-live-grid">
        <section className="app-panel voice-stage-panel">
          <div className="voice-panel-head">
            <div>
              <p className="page-kicker">Step 1</p>
              <h2 className="workspace-panel-title">
                Choose the active source
              </h2>
            </div>
            <div className="brand-badge">Voice only</div>
          </div>

          {isLoadingSources ? (
            <div className="voice-empty-state">
              <p>Loading connected sources...</p>
            </div>
          ) : sources.length === 0 ? (
            <div className="voice-empty-state">
              <p>No sources are connected yet.</p>
              <p>
                Connect a database, spreadsheet, Excel file, or CSV to begin.
              </p>
            </div>
          ) : (
            <div className="voice-source-list">
              {sources.map((source) => (
                <button
                  key={source.id}
                  type="button"
                  className={`voice-source-option ${
                    selectedSourceId === source.id
                      ? "voice-source-option-selected"
                      : ""
                  }`}
                  onClick={() => setSelectedSourceId(source.id)}
                  disabled={sessionLocked}
                >
                  <div className="voice-source-option-head">
                    <div>
                      <p className="voice-source-name">{source.name}</p>
                      <p className="voice-source-detail">{source.detail}</p>
                    </div>
                    <span className="brand-pill">{source.source_type}</span>
                  </div>

                  <div className="voice-source-meta">
                    <span>{sourceKindLabel(source)}</span>
                    <span>{source.status}</span>
                    {source.last_synced_at ? (
                      <span>
                        synced{" "}
                        {new Date(source.last_synced_at).toLocaleDateString()}
                      </span>
                    ) : null}
                  </div>
                </button>
              ))}
            </div>
          )}

          <div className="voice-panel-head">
            <div>
              <p className="page-kicker">Step 2</p>
              <h2 className="workspace-panel-title">Start hands-free mode</h2>
            </div>
            <div
              className={`voice-status-pill voice-status-${currentStatusLabel}`}
            >
              {currentStatusLabel}
            </div>
          </div>

          <div className="voice-live-stage">
            <div
              className={`voice-live-core voice-live-core-${currentStatusLabel}`}
            />

            <div className="voice-live-copy">
              <strong>
                {currentStatusLabel === "live"
                  ? "Live with grounded retrieval"
                  : currentStatusLabel === "muted"
                    ? "Mic muted"
                    : currentStatusLabel === "connecting"
                      ? "Connecting live session"
                      : currentStatusLabel === "ended"
                        ? "Session ended"
                        : currentStatusLabel === "error"
                          ? "Voice session error"
                          : "Ready to start"}
              </strong>
              <p>
                {currentStatusLabel === "live" || currentStatusLabel === "muted"
                  ? "The live Aethex session is active. Data questions should be routed into Vayent's selected-source query tool."
                  : "Starting voice mode uses the server-side Aethex API key and agent ID already configured in your API environment."}
              </p>
              {remoteToolActivity ? (
                <p className="voice-inline-note">{remoteToolActivity}</p>
              ) : null}
            </div>

            <div className="voice-control-actions">
              <button
                type="button"
                className="brand-btn-primary"
                onClick={startVoiceMode}
                disabled={
                  !selectedSourceId ||
                  !capabilities?.aethex_configured ||
                  connectVoiceMutation.isPending ||
                  sessionLocked
                }
              >
                {connectVoiceMutation.isPending
                  ? "Starting..."
                  : "Start Voice Mode"}
              </button>

              <button
                type="button"
                className="brand-btn-secondary"
                onClick={toggleMute}
                disabled={!session || (status !== "live" && status !== "muted")}
              >
                {isMuted ? "Unmute Mic" : "Mute Mic"}
              </button>

              <button
                type="button"
                className="brand-btn-secondary"
                onClick={endVoiceMode}
                disabled={!session || endVoiceMutation.isPending}
              >
                {endVoiceMutation.isPending ? "Ending..." : "End Session"}
              </button>
            </div>
          </div>

          {errorMessage ? (
            <p className="voice-error-banner">{errorMessage}</p>
          ) : null}
          {!capabilities?.aethex_configured ? (
            <p className="voice-inline-note">
              Aethex is not configured on the server yet. Add a valid
              `AETHEX_API_KEY` and `AETHEX_AGENT_ID` to the API environment.
            </p>
          ) : null}
        </section>

        <aside className="app-panel voice-context-panel">
          <div>
            <p className="page-kicker">Live Context</p>
            <h2 className="workspace-panel-title">Current session scope</h2>
          </div>

          <div className="voice-context-block">
            <p className="page-kicker">Active Source</p>
            <strong>{selectedSource?.name ?? "No source selected"}</strong>
            <p>
              {selectedSource?.detail ?? "Choose a connected source to begin."}
            </p>
          </div>

          {session ? (
            <div className="voice-context-block">
              <p className="page-kicker">Session Coverage</p>
              <div className="voice-context-stats">
                <span>{session.source_type}</span>
                <span>{session.schema_kind ?? "source"}</span>
                {session.table_count ? (
                  <span>{session.table_count} sheets</span>
                ) : null}
                {session.row_count ? (
                  <span>{session.row_count.toLocaleString()} rows</span>
                ) : null}
              </div>
            </div>
          ) : null}

          <div className="voice-context-block">
            <p className="page-kicker">Ask Out Loud</p>
            <div className="voice-prompt-list">
              {LIVE_PROMPTS.map((prompt) => (
                <article key={prompt}>
                  <span />
                  <p>{prompt}</p>
                </article>
              ))}
            </div>
          </div>

          <div className="voice-context-block">
            <p className="page-kicker">How It Works</p>
            <div className="voice-flow-list">
              <article>
                <strong>1. Choose source</strong>
                <p>Lock the dataset or connection you want to speak with.</p>
              </article>
              <article>
                <strong>2. Start voice mode</strong>
                <p>
                  Vayent opens a live Aethex session using your API env
                  configuration.
                </p>
              </article>
              <article>
                <strong>3. Ground data questions</strong>
                <p>
                  The live session advertises a source-query tool and relays
                  tool calls back into Vayent&apos;s existing source-first
                  pipeline.
                </p>
              </article>
            </div>
          </div>

          {showLivePromptGuide ? (
            <p className="voice-inline-note">
              Keep this tab open while the live session runs. Source switching
              is locked until the session ends.
            </p>
          ) : null}
        </aside>
      </div>
    </div>
  );
};

export default VoiceConversationPage;
