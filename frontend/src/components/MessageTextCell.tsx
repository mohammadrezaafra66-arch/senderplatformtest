import { useTranslation } from "react-i18next";

type MessageTextCellProps = {
  preview: string | null | undefined;
  hasMore: boolean;
  onMore: () => void;
};

export function MessageTextCell({ preview, hasMore, onMore }: MessageTextCellProps) {
  const { t } = useTranslation();
  const text = preview ?? "";

  return (
    <div className="message-text-cell">
      {text ? (
        <div className="message-text-cell__excerpt">{text}</div>
      ) : (
        <span className="mmp-muted">—</span>
      )}
      {hasMore ? (
        <button type="button" className="message-text-cell__more" onClick={onMore}>
          {t("messageTextMore")}
        </button>
      ) : null}
    </div>
  );
}
