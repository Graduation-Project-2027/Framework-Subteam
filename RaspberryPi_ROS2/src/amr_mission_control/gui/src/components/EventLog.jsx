import { useEffect, useRef, useState, memo } from "react";
import { ChevronDown, CircleAlert, TriangleAlert, Eraser } from "lucide-react";
import { IconButton } from "./ui";
import { cx } from "../lib/cx";

function classify(msg = "") {
  const m = msg.toLowerCase();
  if (m.includes("release")) return "info";
  if (m.includes("error") || m.includes("emergency") || m.includes("failed")) return "error";
  if (m.includes("warn") || m.includes("timeout") || m.includes("cannot")) return "warn";
  return "info";
}

// The backend decorates some messages with warning glyphs; the log draws
// its own icon, so drop them from the text.
const clean = (msg = "") => msg.replace(/\s*\u26A0\uFE0F?\s*/g, " ").trim();

const TIME = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const time = (ts) => (ts ? TIME.format(new Date(ts * 1000)) : "--:--:--");

function EventLog({ entries, collapsible = true, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  const [clearedAt, setClearedAt] = useState(0);
  const scroller = useRef(null);
  const pinned = useRef(true);
  const shown = clearedAt ? entries.filter((e) => (e.timestamp || 0) > clearedAt) : entries;
  const lastErr = [...shown].reverse().find((e) => classify(e.message) === "error");

  useEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [shown.length, open]);

  const onScroll = () => {
    const el = scroller.current;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  };

  return (
    <section className={cx("log", open && "is-open")} aria-label="Event log">
      <header className="log__head">
        <button
          type="button"
          className="log__toggle"
          aria-expanded={open}
          onClick={() => collapsible && setOpen((o) => !o)}
          disabled={!collapsible}
        >
          <h2>Event log</h2>
          <span className="count num">{shown.length}</span>
          {!open && lastErr && <span className="log__peek"><CircleAlert size={14} strokeWidth={2} aria-hidden="true" />{clean(lastErr.message)}</span>}
          {collapsible && <ChevronDown className="log__chev" size={16} strokeWidth={1.75} aria-hidden="true" />}
        </button>
        {open && <IconButton label="Clear view" icon={Eraser} variant="ghost" size="sm" onClick={() => setClearedAt(entries[entries.length - 1]?.timestamp || 0)} disabled={!shown.length} />}
      </header>
      {open && (
        <div className="log__body" ref={scroller} onScroll={onScroll} role="log" aria-live="polite" aria-relevant="additions">
          {shown.length === 0 ? (
            <p className="empty empty--row">Mission events appear here as they happen.</p>
          ) : (
            <ol>
              {shown.map((e, i) => {
                const kind = classify(e.message);
                return (
                  <li key={`${e.timestamp}-${i}`} className={cx("log__line", `is-${kind}`)}>
                    <time className="num">{time(e.timestamp)}</time>
                    {kind === "error" && <CircleAlert size={14} strokeWidth={2} aria-label="Error" />}
                    {kind === "warn" && <TriangleAlert size={14} strokeWidth={2} aria-label="Warning" />}
                    <span>{clean(e.message)}</span>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}

export default memo(EventLog);
