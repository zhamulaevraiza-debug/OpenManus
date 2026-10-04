import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { useI18n } from "@/i18n";
import { cn } from "@/utils/cn";

import { ToastContext, type ToastApi, type ToastTone } from "./toast";

interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}

const DURATION: Record<ToastTone, number> = { success: 3500, info: 4000, error: 6500 };
const ICONS = { success: CircleCheck, error: CircleAlert, info: Info };
const TONES: Record<ToastTone, string> = {
  success: "text-success",
  error: "text-danger",
  info: "text-accent-text",
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const show = useCallback((message: string, tone: ToastTone = "info") => {
    const id = nextId.current++;
    setToasts((current) => {
      // Collapse identical messages and keep the stack short.
      const rest = current.filter((toast) => toast.message !== message);
      return [...rest, { id, message, tone }].slice(-3);
    });
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      show,
      success: (message) => show(message, "success"),
      error: (message) => show(message, "error"),
      info: (message) => show(message, "info"),
    }),
    [show],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      {createPortal(
        <div
          aria-live="polite"
          aria-relevant="additions"
          className="pointer-events-none fixed inset-x-0 top-0 z-[60] flex flex-col items-center gap-2 px-4 pt-[calc(env(safe-area-inset-top)+0.75rem)]"
        >
          {toasts.map((toast) => (
            <Toast key={toast.id} toast={toast} onDismiss={dismiss} />
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}

function Toast({ toast, onDismiss }: { toast: ToastItem; onDismiss: (id: number) => void }) {
  const { t } = useI18n();
  const Icon = ICONS[toast.tone];
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(toast.id), DURATION[toast.tone]);
    return () => clearTimeout(timer);
  }, [toast, onDismiss]);
  return (
    <div
      role={toast.tone === "error" ? "alert" : "status"}
      className="pointer-events-auto flex w-full max-w-sm animate-pop-in items-start gap-3 rounded-2xl border border-border bg-surface py-3 pr-2 pl-4 text-sm text-fg shadow-lg"
    >
      <Icon className={cn("mt-0.5 size-[18px] shrink-0", TONES[toast.tone])} aria-hidden />
      <p className="min-w-0 flex-1 py-px leading-relaxed">{toast.message}</p>
      <button
        type="button"
        onClick={() => onDismiss(toast.id)}
        aria-label={t("common.close")}
        className="-my-1 inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-fg-subtle hover:bg-surface-2 hover:text-fg"
      >
        <X className="size-4" />
      </button>
    </div>
  );
}
