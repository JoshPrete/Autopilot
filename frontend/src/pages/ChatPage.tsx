import { useState, useRef, useEffect } from "react";
import { getToken, apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { ChatAgendaItem, ChatAgendaResponse } from "../types/api";

interface Message {
  role: "user" | "assistant";
  content: string;
}

export function ChatPage() {
  const { user } = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [agenda, setAgenda] = useState<ChatAgendaItem[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Load curiosity agenda on mount and pre-seed an opener if available
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
        // silently ignore — chat still works without agenda
      });
  }, [user]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send() {
    if (!input.trim() || streaming || !user) return;
    const userMsg: Message = { role: "user", content: input.trim() };
    const nextMessages = [...messages, userMsg];
    setMessages(nextMessages);
    setInput("");
    setError("");
    setStreaming(true);

    const assistantMsg: Message = { role: "assistant", content: "" };
    setMessages([...nextMessages, assistantMsg]);

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

      const reader = resp.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            const chunk = line.slice(6);
            if (chunk === "[DONE]") continue;
            setMessages((prev) => {
              const updated = [...prev];
              updated[updated.length - 1] = {
                ...updated[updated.length - 1],
                content: updated[updated.length - 1].content + chunk,
              };
              return updated;
            });
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

  return (
    <div className="chat-page">
      <h1 className="page-title">Chat</h1>

      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">
            Ask anything about operations, staffing, margins, or tomorrow&apos;s plan.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-bubble chat-bubble-${m.role}`}>
            <div className="chat-bubble-label">{m.role === "user" ? user?.name ?? "You" : "Autopilot"}</div>
            <div className="chat-bubble-content">{m.content}{i === messages.length - 1 && streaming && m.role === "assistant" && <span className="chat-cursor" />}</div>
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
