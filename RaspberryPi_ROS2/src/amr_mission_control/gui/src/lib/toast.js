import { createContext, useContext } from "react";

export const ToastCtx = createContext(() => {});

/** toast({ title, body, tone: "info" | "error", action: { label, onClick } }) */
export const useToast = () => useContext(ToastCtx);
