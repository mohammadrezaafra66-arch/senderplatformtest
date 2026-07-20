import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, Button, EmptyState, FormField, inputClassName, tableClassName, TableWrap } from "@/components/ui";
import { apiFetch, ApiError } from "@/lib/api";
import { toJalaliDateTime } from "@/utils/jalali";

type RubikaContentScheduleManagerProps = {
  canManage: boolean;
};

type ContentScheduleItem = {
  id: number;
  caption: string | null;
  media_path: string | null;
  content_type: string;
  scheduled_at: string;
  published: boolean;
  published_at: string | null;
  error_message: string | null;
  created_at: string;
};

type ContentScheduleListResult = {
  items: ContentScheduleItem[];
  total_count: number;
};

export function RubikaContentScheduleManager({ canManage }: RubikaContentScheduleManagerProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<ContentScheduleItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [newCaption, setNewCaption] = useState("");
  const [newMediaPath, setNewMediaPath] = useState("");
  const [newScheduledAt, setNewScheduledAt] = useState("");
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch("/rubika/content-schedule");
      const result = (await response.json()) as ContentScheduleListResult;
      setItems(result.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("rubikaLoadError"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newScheduledAt.trim()) {
      setError(t("requiredFields"));
      return;
    }
    setCreating(true);
    setError(null);
    setNotice(null);
    try {
      await apiFetch("/rubika/content-schedule", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          caption: newCaption.trim() || null,
          media_path: newMediaPath.trim() || null,
          content_type: "Picture",
          scheduled_at: newScheduledAt.length === 16 ? newScheduledAt + ":00" : newScheduledAt,
        }),
      });
      setNewCaption("");
      setNewMediaPath("");
      setNewScheduledAt("");
      setNotice(t("rubikaContentScheduleCreated"));
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(item: ContentScheduleItem) {
    setError(null);
    try {
      await apiFetch(`/rubika/content-schedule/${item.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    }
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {error ? <Alert variant="error">{error}</Alert> : null}
      {notice ? <Alert variant="success">{notice}</Alert> : null}

      {canManage ? (
        <form
          onSubmit={(e) => void handleCreate(e)}
          style={{
            display: "grid",
            gap: 10,
            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            border: "1px solid rgba(0,0,0,0.1)",
            borderRadius: 10,
            padding: 12,
          }}
        >
          <FormField label={t("rubikaContentScheduleCaption")}>
            <input
              className={inputClassName}
              value={newCaption}
              onChange={(e) => setNewCaption(e.target.value)}
            />
          </FormField>
          <FormField label={t("rubikaContentScheduleMediaPath")}>
            <input
              className={inputClassName}
              value={newMediaPath}
              onChange={(e) => setNewMediaPath(e.target.value)}
              style={{ direction: "ltr", textAlign: "left" }}
            />
          </FormField>
          <FormField label={t("rubikaContentScheduleScheduledAt")}>
            <input
              className={inputClassName}
              type="datetime-local"
              value={newScheduledAt}
              onChange={(e) => setNewScheduledAt(e.target.value)}
              style={{ direction: "ltr", textAlign: "left" }}
            />
          </FormField>
          <div style={{ alignSelf: "end" }}>
            <Button type="submit" disabled={creating}>
              {creating ? t("loading") : t("rubikaContentScheduleAdd")}
            </Button>
          </div>
        </form>
      ) : null}

      {loading && items.length === 0 ? (
        <EmptyState>{t("loading")}</EmptyState>
      ) : items.length === 0 ? (
        <EmptyState>{t("rubikaContentScheduleEmpty")}</EmptyState>
      ) : (
        <TableWrap>
          <table className={tableClassName}>
            <thead>
              <tr>
                <th>{t("rubikaContentScheduleCaption")}</th>
                <th>{t("rubikaContentScheduleScheduledAt")}</th>
                <th>{t("rubikaContentScheduleStatus")}</th>
                <th>{t("rubikaContentScheduleError")}</th>
                {canManage ? <th>{t("actions")}</th> : null}
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>{item.caption ?? "—"}</td>
                  <td style={{ direction: "ltr", textAlign: "left", fontSize: 12 }}>
                    {toJalaliDateTime(item.scheduled_at)}
                  </td>
                  <td>
                    {item.published
                      ? t("rubikaContentSchedulePublished")
                      : t("rubikaContentSchedulePending")}
                  </td>
                  <td style={{ fontSize: 12, color: item.error_message ? "rgb(153,27,27)" : undefined }}>
                    {item.error_message ?? "—"}
                  </td>
                  {canManage ? (
                    <td>
                      <Button type="button" size="sm" variant="ghost" onClick={() => void handleDelete(item)}>
                        {t("delete")}
                      </Button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </div>
  );
}
