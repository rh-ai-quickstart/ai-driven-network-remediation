import { useState } from "react";

// Matches the section headers ran-chatbot-service's format_chat_reply() always
// emits (see hub/ran-chatbot-service/src/ran_chatbot_service/chat.py) — every
// /api/chat reply has this shape, not just an on-demand "brief" like the NOC
// chatbot, so every reply is parsed into sections by default.
const REPLY_HEADERS = ["Summary", "Root Cause", "Recommended Fix", "Model Output"];

const QUICK_ASKS = [
  "What's wrong with cell 42?",
  "Summarize current RAN anomalies",
  "What's the recommended fix for the latest issue?",
  "Which anomaly type is most common right now?",
];

function parseStructuredReply(text) {
  if (!text || typeof text !== "string") return null;

  // Extract category tag if present (e.g., "[Interference] Summary:")
  const categoryMatch = text.match(/^\[([^\]]+)\]\s*/);
  const categoryTag = categoryMatch ? categoryMatch[1] : null;
  const cleanText = categoryMatch ? text.slice(categoryMatch[0].length) : text;

  const sections = [];
  let current = null;
  for (const raw of cleanText.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const header = REPLY_HEADERS.find((h) => line === `${h}:`);
    if (header) {
      current = { title: header, lines: [] };
      sections.push(current);
      continue;
    }
    if (current) current.lines.push(line);
  }
  if (sections.length < 2) return null;
  return { categoryTag, sections };
}

export function ChatPanel({ baseUrl }) {
  const [message, setMessage] = useState("");
  const [sessionId] = useState(() => `session-${Date.now()}`);
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      text: "RAN Chat online. Ask about detected cell anomalies, root causes, or recommended fixes.",
    },
  ]);
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(false);

  async function sendMessage(outgoing) {
    if (!outgoing.trim() || loading) return;
    setMessages((prev) => [...prev, { role: "user", text: outgoing }]);
    setMessage("");
    setLoading(true);
    try {
      const url = baseUrl ? `${baseUrl}/api/chat` : "/api/chat";
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: outgoing, session_id: sessionId }),
      });
      if (!res.ok) {
        throw new Error(`Chat request failed (${res.status})`);
      }
      const contentType = res.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) {
        throw new Error(`Non-JSON response (${res.status})`);
      }
      const data = await res.json();
      const chatDeps = data._deps || { status: "ok" };
      const degradedNote =
        chatDeps.status === "degraded"
          ? ` [⚠ Partial — ${(chatDeps.unavailable || []).join(", ")} unavailable]`
          : "";
      setMessages((prev) => [
        ...prev,
        { role: "assistant", text: (data.reply || "No response") + degradedNote },
      ]);

      const model = data.model || {};
      const context = data.context || {};
      setMeta({
        modelName: model.name || "n/a",
        modelSource: model.source || "unknown",
        anomalyCount: context.anomaly_count ?? 0,
        degraded: chatDeps.status === "degraded",
      });
    } catch (err) {
      const detail = err?.message || "Chatbot endpoint unreachable.";
      setMessages((prev) => [...prev, { role: "assistant", text: detail }]);
    } finally {
      setLoading(false);
    }
  }

  async function submit(e) {
    e.preventDefault();
    await sendMessage(message.trim());
  }

  async function runQuickAsk(prompt) {
    await sendMessage(prompt);
  }

  return (
    <section className="panel">
      <h2>RAN Chat</h2>
      <div className="quick-asks">
        {QUICK_ASKS.map((ask) => (
          <button
            key={ask}
            type="button"
            onClick={() => runQuickAsk(ask)}
            disabled={loading}
            className="quick-ask-btn"
          >
            {ask}
          </button>
        ))}
      </div>
      <form onSubmit={submit} className="chat">
        <input
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="Ask about a cell, band, or anomaly type"
        />
        <button type="submit" disabled={loading}>
          {loading ? "Sending..." : "Send"}
        </button>
      </form>
      <div className="chat-log">
        {messages.map((item, idx) => {
          const parsed = item.role === "assistant" ? parseStructuredReply(item.text) : null;
          return (
            <article key={`${item.role}-${idx}`} className={`bubble ${item.role}`}>
              <strong>{item.role === "user" ? "You" : "RAN Assistant"}</strong>
              {parsed ? (
                <div className="exec-reply">
                  {parsed.categoryTag && (
                    <div className="category-tag-badge">
                      <span className="category-tag">[{parsed.categoryTag}]</span>
                    </div>
                  )}
                  {parsed.sections.map((section) => (
                    <section key={section.title} className="exec-section">
                      <h4>{section.title}</h4>
                      <ul>
                        {(section.lines || []).map((line, lineIdx) => (
                          <li key={`${section.title}-${lineIdx}`}>{line}</li>
                        ))}
                      </ul>
                    </section>
                  ))}
                </div>
              ) : (
                <p>{item.text}</p>
              )}
            </article>
          );
        })}
      </div>
      {meta && (
        <div className="chat-meta">
          <p>
            Model: <strong>{meta.modelName}</strong> ({meta.modelSource}) · Known
            anomalies: <strong>{meta.anomalyCount}</strong>
          </p>
        </div>
      )}
    </section>
  );
}
