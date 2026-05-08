/**
 * FeedbackConsole — Scrollable log window with timestamped, color-coded messages.
 */
import { useEffect, useRef } from "react";

export default function FeedbackConsole({ feedbackLog }) {
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [feedbackLog]);

  const formatTime = (ts) => {
    if (!ts) return "--:--:--";
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString("en-GB", { hour12: false });
  };

  const getClass = (msg) => {
    if (!msg) return "";
    const lower = msg.toLowerCase();
    if (lower.includes("error") || lower.includes("⚠") || lower.includes("emergency"))
      return "console__msg--error";
    if (lower.includes("warn") || lower.includes("timeout"))
      return "console__msg--warn";
    return "";
  };

  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__title">📟 Feedback</span>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{feedbackLog.length} entries</span>
      </div>
      <div className="panel__body console">
        {feedbackLog.length === 0 && (
          <div className="empty-state">
            <span>No feedback yet</span>
          </div>
        )}
        {feedbackLog.map((entry, i) => (
          <div key={i} className="console__line">
            <span className="console__time">{formatTime(entry.timestamp)}</span>
            <span className={`console__msg ${getClass(entry.message)}`}>{entry.message}</span>
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}
