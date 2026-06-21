import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Toast, type ToastVariant } from "@/components/Toast";
import { Button } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { triggerWhatsAppWarmup } from "@/lib/whatsapp-api";

export function WhatsAppWarmupTrigger() {
  const { t } = useTranslation();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [toastOpen, setToastOpen] = useState(false);
  const [toastVariant, setToastVariant] = useState<ToastVariant>("success");
  const [toastMessage, setToastMessage] = useState("");

  const showToast = useCallback((variant: ToastVariant, message: string) => {
    setToastVariant(variant);
    setToastMessage(message);
    setToastOpen(true);
  }, []);

  const handleOpenDialog = () => {
    setDialogOpen(true);
  };

  const handleCloseDialog = () => {
    if (submitting) {
      return;
    }
    setDialogOpen(false);
  };

  const handleConfirm = async () => {
    setSubmitting(true);
    try {
      const result = await triggerWhatsAppWarmup();
      setDialogOpen(false);
      showToast(
        "success",
        t("warmupSuccess", { count: result.pairedAccounts }),
      );
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.status === 429) {
          showToast("error", t("warmupRateLimited"));
        } else {
          showToast("error", error.message || t("warmupGenericError"));
        }
      } else {
        showToast("error", t("warmupGenericError"));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <div className="mmp-warmup-trigger">
        <p className="mmp-muted mmp-warmup-trigger__hint">{t("warmupDescription")}</p>
        <Button variant="primary" onClick={handleOpenDialog}>
          {t("warmupTriggerButton")}
        </Button>
      </div>

      <ConfirmDialog
        open={dialogOpen}
        title={t("warmupConfirmTitle")}
        message={t("warmupConfirmMessage")}
        cancelLabel={t("warmupCancel")}
        confirmLabel={submitting ? t("loading") : t("warmupExecute")}
        confirmLoading={submitting}
        cancelDisabled={submitting}
        onCancel={handleCloseDialog}
        onConfirm={() => void handleConfirm()}
      />

      <Toast
        open={toastOpen}
        variant={toastVariant}
        message={toastMessage}
        onClose={() => setToastOpen(false)}
      />
    </>
  );
}
