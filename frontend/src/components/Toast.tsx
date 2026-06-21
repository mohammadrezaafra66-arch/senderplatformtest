import { useEffect } from "react";

export type ToastVariant = "success" | "error";

type ToastProps = {
  open: boolean;
  variant: ToastVariant;
  message: string;
  onClose: () => void;
  autoHideMs?: number;
};

export function Toast({
  open,
  variant,
  message,
  onClose,
  autoHideMs = 6000,
}: ToastProps) {
  useEffect(() => {
    if (!open) {
      return;
    }
    const timer = window.setTimeout(onClose, autoHideMs);
    return () => window.clearTimeout(timer);
  }, [open, autoHideMs, onClose]);

  if (!open || !message) {
    return null;
  }

  return (
    <div
      className={`mmp-toast mmp-toast--${variant}`}
      role={variant === "error" ? "alert" : "status"}
      aria-live="polite"
    >
      <span>{message}</span>
      <button type="button" className="mmp-toast__close" onClick={onClose} aria-label="بستن">
        ×
      </button>
    </div>
  );
}
