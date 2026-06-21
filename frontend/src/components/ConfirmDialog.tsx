import type { ReactNode } from "react";

import { Button } from "@/components/ui";

type ConfirmDialogProps = {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel: string;
  cancelLabel: string;
  confirmLoading?: boolean;
  cancelDisabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel,
  cancelLabel,
  confirmLoading = false,
  cancelDisabled = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  if (!open) {
    return null;
  }

  return (
    <div className="mmp-modal" role="presentation" onClick={onCancel}>
      <div
        className="mmp-modal__dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mmp-confirm-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h3 id="mmp-confirm-title" className="mmp-modal__title">
          {title}
        </h3>
        <div className="mmp-modal__body">{message}</div>
        <div className="mmp-modal__actions">
          <Button variant="ghost" disabled={cancelDisabled || confirmLoading} onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button variant="primary" disabled={confirmLoading} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
