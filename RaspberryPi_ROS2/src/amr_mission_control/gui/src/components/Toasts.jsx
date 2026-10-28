import { useCallback, useState } from "react";
import { Toast } from "radix-ui";
import { X } from "lucide-react";
import { cx } from "../lib/cx";
import { ToastCtx } from "../lib/toast";

export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const toast = useCallback((t) => {
    const id = Math.random().toString(36).slice(2);
    setItems((list) => [...list.slice(-3), { id, duration: t.action ? 8000 : 4000, ...t }]);
  }, []);
  const drop = (id) => setItems((list) => list.filter((t) => t.id !== id));

  return (
    <ToastCtx.Provider value={toast}>
      <Toast.Provider swipeDirection="right">
        {children}
        {items.map((t) => (
          <Toast.Root
            key={t.id}
            className={cx("toast", t.tone === "error" && "toast--error")}
            duration={t.duration}
            onOpenChange={(open) => { if (!open) drop(t.id); }}
          >
            <div className="toast__text">
              <Toast.Title className="toast__title">{t.title}</Toast.Title>
              {t.body && <Toast.Description className="toast__body">{t.body}</Toast.Description>}
            </div>
            {t.action && (
              <Toast.Action asChild altText={t.action.label}>
                <button type="button" className="toast__action" onClick={t.action.onClick}>{t.action.label}</button>
              </Toast.Action>
            )}
            <Toast.Close className="toast__close" aria-label="Dismiss"><X size={14} strokeWidth={2} aria-hidden="true" /></Toast.Close>
          </Toast.Root>
        ))}
        <Toast.Viewport className="toasts" />
      </Toast.Provider>
    </ToastCtx.Provider>
  );
}
