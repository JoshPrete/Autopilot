import { useState, useRef, useEffect, useCallback } from "react";
import { getToken, apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type {
  ChatAgendaItem,
  ChatAgendaResponse,
  ChatMessageResponse,
  DataHealthComponent,
  DataHealthResponse,
} from "../types/api";

interface Message {
  role: "user" | "assistant";
  content: string;
  warnings?: string[];
  follow_up_questions?: string[];
  follow_up_hint?: string | null;
  blocked?: boolean;
}

function pickAssistantContent(payload: ChatMessageResponse): string {
  const warnings = payload.warnings ?? [];
  const followUps = payload.follow_up_questions ?? [];
  const hasMeta = warnings.length > 0 || followUps.length > 0;

  if (payload.blocked) {
    return payload.final_answer || payload.content || payload.error || "";
  }

  if (hasMeta) {
    return payload.draft_answer || payload.final_answer || payload.content || "";
  }

  return payload.final_answer || payload.content || payload.draft_answer || "";
}

export function ChatPage() {
  const { user } = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [agenda, setAgenda] = useState<ChatAgendaItem[]>([]);
  const [dataHealth, setDataHealth] = useState<DataHealthResponse | null>(null);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const updateAssistantMessage = useCallback((payload: ChatMessageResponse) => {
    setMessages((prev) => {
      const updated = [...prev];
      const idx = updated.length - 1;
      if (idx < 0) return prev;
      updated[idx] = {
        ...updated[idx],
        role: "assistant",
        content: pickAssistantContent(payload),
        warnings: payload.warnings ?? [],
        follow_up_questions: payload.follow_up_questions ?? [],
        follow_up_hint: payload.follow_up_hint ?? null,
        blocked: Boolean(payload.blocked),
      };
      return updated;
    });
  }, []);

  useEffect(() => {
    if (!user) return;
    apiFetch<ChatAgendaResponse>(`/api/sites/${user.site_id}/chat/agenda`)
      .then((data) => {
        if (data.opener) {
          setMessages([{ role: "assistant", content: data.opener }]);
        }
        if (data.agenda?.length > 1) {
          setAgenda(data.agenda.slice(1, 4));
        }
      })
      .catch(() => {
        // chat still works without agenda
      });
  }, [user]);

  useEffect(() => {
    if (!user) return;
    apiFetch<DataHealthResponse>(`/api/sites/${user.site_id}/analysis/data-health`)
      .then(setDataHealth)
      .catch(() => {
        // chat still works without trust context
      });
  }, [user]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send() {
    if (!input.trim() || streaming || !user) return;
    const userMsg: Message = { role: "user", content: input.trim() };
    const nextMessages = [...messages, userMsg];
    setMessages([...nextMessages, { role: "assistant", content: "" }]);
    setInput("");
    setError("");
    setStreaming(true);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      const resp = await fetch(`/api/sites/${user.site_id}/chat/message`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${getToken()}`,
        },
        body: JSON.stringify({ messages: nextMessages }),
        signal: ctrl.signal,
      });

      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed: ${resp.status}`);
      }

      const reader = resp.body?.getReader();
      if (!reader) {
        throw new Error("No response body received from chat API.");
      }

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;

          let payload: ChatMessageResponse;
          try {
            payload = JSON.parse(raw) as ChatMessageResponse;
          } catch {
            updateAssistantMessage({ content: raw });
            continue;
          }

          if (payload.done) continue;

          updateAssistantMessage(payload);
          if (payload.error && !payload.final_answer && !payload.content) {
            setError(payload.error);
          }
        }
      }
    } catch (err: unknown) {
      if ((err as Error).name !== "AbortError") {
        setError((err as Error).message);
        setMessages((prev) => prev.slice(0, -1));
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  const showChips = agenda.length > 0 && messages.length <= 1 && !streaming;
  const trustComponents = (dataHealth?.components ?? []).filter((component) =>
    ["square_orders", "daily_profitability", "deputy_rosters", "predictions", "xero_financial_facts"].includes(component.source),
  );

  return (
    <div className="chat-page">
      <h1 className="page-title">Chat</h1>

      {dataHealth && (
        <div className={`chat-trust-strip status-${dataHealth.status ?? "unknown"}`}>
          <div className="chat-trust-overall">
            <span>Data trust</span>
            <span className={`chat-trust-badge status-${dataHealth.status ?? "unknown"}`}>
              {dataHealth.status ?? "unknown"}
            </span>
            <span>{dataHealth.score != null ? `${Math.round(dataHealth.score * 100)}%` : "--"}</span>
          </div>
          <div className="chat-trust-pills">
            {trustComponents.map((component) => (
              <div key={component.source} className="chat-trust-pill">
                <span className={`chat-trust-dot status-${component.status ?? "unknown"}`} />
                <span className="chat-trust-copy">
                  <strong>{labelForSource(component)}</strong>
                  <span>
                    {component.latest_date ?? "--"}
                    {component.age_days != null ? ` · ${component.age_days}d` : ""}
                  </span>
                  {healthNote(component) && <span className="chat-trust-note">{healthNote(component)}</span>}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">
            Ask anything about operations, staffing, margins, or tomorrow&apos;s plan.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-bubble chat-bubble-${m.role}`}>
            <div className="chat-bubble-label">{m.role === "user" ? user?.name ?? "You" : "Autopilot"}</div>
            <div className="chat-bubble-content">
              <div className="chat-answer">
                {m.content}
                {i === messages.length - 1 && streaming && m.role === "assistant" && <span className="chat-cursor" />}
              </div>
              {m.role === "assistant" && !!m.warnings?.length && (
                <div className="chat-meta-card chat-meta-warning">
                  <div className="chat-meta-title">Confidence note</div>
                  <ul>
                    {m.warnings.map((warning, idx) => (
                      <li key={idx}>{warning}</li>
                    ))}
                  </ul>
                </div>
              )}
              {m.role === "assistant" && !!m.follow_up_questions?.length && (
                <div className="chat-meta-card chat-meta-followup">
                  <div className="chat-meta-title">
                    {m.blocked ? "What needs fixing first" : "To improve this further"}
                  </div>
                  <ul>
                    {m.follow_up_questions.map((question, idx) => (
                      <li key={idx}>{question}</li>
                    ))}
                  </ul>
                  {m.follow_up_hint && <div className="chat-meta-hint">{m.follow_up_hint}</div>}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {error && <div className="error-banner">{error}</div>}

      {showChips && (
        <div className="agenda-chips">
          {agenda.map((item, i) => (
            <button
              key={i}
              className="agenda-chip"
              onClick={() => setInput(item.question)}
            >
              {item.question}
            </button>
          ))}
        </div>
      )}

      <div className="chat-input-row">
        <textarea
          className="chat-input"
          rows={2}
          placeholder="Ask a question..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={streaming}
        />
        {streaming ? (
          <button className="btn-stop" onClick={stop}>Stop</button>
        ) : (
          <button className="btn-send" onClick={send} disabled={!input.trim()}>Send</button>
        )}
      </div>
    </div>
  );
}

function labelForSource(component: DataHealthComponent): string {
  const labels: Record<string, string> = {
    square_orders: "Square",
    daily_profitability: "Profit",
    deputy_rosters: "Deputy",
    predictions: "Predictions",
    xero_financial_facts: "Xero Facts",
  };
  return labels[component.source] ?? component.source;
}

function healthNote(component: DataHealthComponent): string | null {
  const blockers = Array.isArray(component.blockers) ? component.blockers : [];
  const limitations = Array.isArray(component.limitations) ? component.limitations : [];
  return blockers[0] ?? limitations[0] ?? null;
}
