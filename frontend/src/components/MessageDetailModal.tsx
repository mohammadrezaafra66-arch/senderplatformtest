import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui";
import type { MessageLogDetail } from "@/types/campaign";
import { toJalaliDateTime } from "@/utils/jalali";

type MessageDetailModalProps = {
  open: boolean;
  loading?: boolean;
  error?: string | null;
  detail: MessageLogDetail | null;
  onClose: () => void;
};

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="message-detail-meta__row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function MessageDetailModal({
  open,
  loading = false,
  error = null,
  detail,
  onClose,
}: MessageDetailModalProps) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  if (!open) {
    return null;
  }

  async function handleCopy() {
    const text = detail?.final_text ?? "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  const recipientName = detail
    ? [detail.first_name, detail.last_name].filter(Boolean).join(" ") || "—"
    : "—";
  const sender =
    detail?.sender_account?.label ||
    detail?.sender_account?.account_identifier ||
    (detail?.account_id != null ? `#${detail.account_id}` : "—");

  return (
    <div className="mmp-modal" role="presentation" onClick={onClose}>
      <div
        className="mmp-modal__dialog mmp-modal__dialog--wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mmp-message-detail-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h3 id="mmp-message-detail-title" className="mmp-modal__title">
          {t("fullMessage")}
        </h3>
        {loading ? <p className="mmp-muted">{t("loading")}</p> : null}
        {error ? <p className="mmp-muted">{error}</p> : null}
        {detail ? (
          <>
            <p className="mmp-muted">{t("committedFinalHint")}</p>
            <pre className="message-detail-text">{detail.final_text ?? ""}</pre>
            <div className="message-detail-meta">
              <MetaRow label={t("campaignId")} value={String(detail.campaign_id)} />
              <MetaRow label={t("name")} value={recipientName} />
              <MetaRow label={t("phone")} value={detail.phone ?? "—"} />
              <MetaRow label={t("sender")} value={sender} />
              <MetaRow label="render" value={detail.render_status} />
              <MetaRow label="send" value={detail.send_status} />
              <MetaRow
                label={t("renderVersion")}
                value={detail.render_version ?? "—"}
              />
              <MetaRow
                label={t("renderBatch")}
                value={detail.render_batch_id ?? "—"}
              />
              <MetaRow
                label={t("finalTextHash")}
                value={detail.final_text_sha256 ?? "—"}
              />
              <MetaRow
                label={t("useGpt")}
                value={detail.gpt?.use_gpt || detail.use_gpt ? t("yes") : t("no")}
              />
              {detail.gpt?.use_gpt ? (
                <>
                  <MetaRow
                    label={t("gptVariationId")}
                    value={detail.gpt.variation_id ?? "—"}
                  />
                  <MetaRow
                    label="generation_batch_id"
                    value={detail.gpt.generation_batch_id ?? "—"}
                  />
                </>
              ) : null}
              <MetaRow
                label={t("includeProducts")}
                value={
                  detail.products?.include_products || detail.include_products
                    ? t("yes")
                    : t("no")
                }
              />
              <MetaRow
                label={t("updatedAt")}
                value={toJalaliDateTime(detail.updated_at)}
              />
              {detail.rendered_at ? (
                <MetaRow
                  label={t("renderedAt")}
                  value={toJalaliDateTime(detail.rendered_at)}
                />
              ) : null}
              {detail.sent_at ? (
                <MetaRow
                  label={t("sentAt")}
                  value={toJalaliDateTime(detail.sent_at)}
                />
              ) : null}
              {detail.error_code ? (
                <MetaRow label={t("error")} value={detail.error_code} />
              ) : null}
            </div>
            {detail.products?.products && detail.products.products.length > 0 ? (
              <div className="message-detail-products">
                <strong>{t("selectedProducts")}</strong>
                <ul>
                  {detail.products.products.map((product) => (
                    <li key={`${product.external_id ?? ""}-${product.name ?? ""}`}>
                      {product.name} — {product.display_price ?? product.price}{" "}
                      ({product.currency ?? ""})
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </>
        ) : null}
        <div className="mmp-modal__actions">
          <Button variant="ghost" onClick={onClose}>
            {t("close")}
          </Button>
          <Button
            variant="primary"
            disabled={!detail?.final_text}
            onClick={() => void handleCopy()}
          >
            {copied ? t("copied") : t("copyText")}
          </Button>
        </div>
      </div>
    </div>
  );
}
