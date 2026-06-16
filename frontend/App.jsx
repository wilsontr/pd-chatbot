// src/App.jsx
import { useState, useRef, useEffect } from "react";

const SOURCE_LABELS = {
  msp_manual: "MSP Manual",
  iem_reference: "IEM Object Reference"
};

const SOURCE_COLORS = {
  msp_manual: "#4a90d9",
  iem_reference: "#7cb87c"
};

export default function App() {
  const [messages, setMessages] = useState([]);
  const [history, setHistory] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send() {
    if (!input.trim() || loading) return;
    const question = input;
    setInput("");
    setLoading(true);

    setMessages(prev => [...prev, {
      role: "user", content: question, sources: [], query_type: null
    }]);

    try {
      const res = await fetch("http://localhost:8000/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question, history })
      });
      const data = await res.json();
      setHistory(data.history);
      setMessages(prev => [...prev, {
        role: "assistant",
        content: data.answer,
        sources: data.sources,
        query_type: data.query_type
      }]);
    } catch (err) {
      setMessages(prev => [...prev, {
        role: "assistant",
        content: "Error reaching the server.",
        sources: [],
        query_type: null
      }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 800, margin: "0 auto", padding: 24, fontFamily: "sans-serif" }}>
      <h1>Pure Data Documentation Assistant</h1>
      <p style={{ color: "#666", fontSize: 14 }}>
        Answers from the MSP Manual and IEM Object Reference
      </p>

      <div style={{ minHeight: 400, marginBottom: 16 }}>
        {messages.map((msg, i) => (
          <div key={i} style={{ marginBottom: 20 }}>
            <strong>{msg.role === "user" ? "You" : "Assistant"}:</strong>
            {msg.query_type && (
              <span style={{
                marginLeft: 8, fontSize: 11, color: "#999",
                textTransform: "uppercase", letterSpacing: 1
              }}>
                {msg.query_type.replace("_", " ")}
              </span>
            )}
            <p style={{ margin: "4px 0", whiteSpace: "pre-wrap" }}>{msg.content}</p>
            {msg.sources?.length > 0 && (
              <details style={{ marginTop: 4 }}>
                <summary style={{ cursor: "pointer", color: "#666", fontSize: 13 }}>
                  Sources ({msg.sources.length})
                </summary>
                <ul style={{ margin: "4px 0", paddingLeft: 20, fontSize: 12 }}>
                  {msg.sources.map((s, j) => (
                    <li key={j} style={{ marginBottom: 4 }}>
                      <span style={{
                        display: "inline-block",
                        background: SOURCE_COLORS[s.source],
                        color: "white",
                        borderRadius: 3,
                        padding: "1px 5px",
                        fontSize: 10,
                        marginRight: 6
                      }}>
                        {SOURCE_LABELS[s.source]}
                      </span>
                      <a href={s.url} target="_blank" rel="noreferrer">
                        {s.heading_path}
                      </a>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        ))}
        {loading && <p style={{ color: "#999" }}>Thinking...</p>}
        <div ref={bottomRef} />
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === "Enter" && send()}
          placeholder="Ask about Pure Data..."
          style={{ flex: 1, padding: 8, fontSize: 16 }}
        />
        <button onClick={send} disabled={loading} style={{ padding: "8px 16px" }}>
          Send
        </button>
      </div>
    </div>
  );
}
