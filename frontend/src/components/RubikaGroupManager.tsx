import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, Button, EmptyState, FormField, inputClassName, tableClassName, TableWrap, textareaClassName } from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  createRubikaGroup,
  deleteRubikaGroup,
  fetchRubikaGroupMessages,
  fetchRubikaGroups,
  updateRubikaGroup,
} from "@/lib/rubika-api";
import type { RubikaGroupItem, RubikaGroupMessageItem } from "@/types/rubika";
import { toJalaliDateTime } from "@/utils/jalali";

type RubikaGroupManagerProps = {
  canManage: boolean;
};

function parseCsv(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export function RubikaGroupManager({ canManage }: RubikaGroupManagerProps) {
  const { t } = useTranslation();
  const [groups, setGroups] = useState<RubikaGroupItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [newGuid, setNewGuid] = useState("");
  const [newName, setNewName] = useState("");
  const [newKeywords, setNewKeywords] = useState("");
  const [newKeywordResponse, setNewKeywordResponse] = useState("");
  const [newRedKeywords, setNewRedKeywords] = useState("");
  const [creating, setCreating] = useState(false);

  const [expandedGroupId, setExpandedGroupId] = useState<number | null>(null);
  const [messages, setMessages] = useState<RubikaGroupMessageItem[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchRubikaGroups();
      setGroups(result.items);
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
    if (!newGuid.trim()) {
      setError(t("requiredFields"));
      return;
    }
    setCreating(true);
    setError(null);
    setNotice(null);
    try {
      await createRubikaGroup({
        group_guid: newGuid.trim(),
        group_name: newName.trim() || null,
        keywords: parseCsv(newKeywords),
        keyword_response: newKeywordResponse.trim() || null,
        red_keywords: parseCsv(newRedKeywords),
      });
      setNewGuid("");
      setNewName("");
      setNewKeywords("");
      setNewKeywordResponse("");
      setNewRedKeywords("");
      setNotice(t("rubikaGroupCreated"));
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setCreating(false);
    }
  }

  async function handleToggleActive(group: RubikaGroupItem) {
    setError(null);
    try {
      await updateRubikaGroup(group.id, { is_active: !group.is_active });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    }
  }

  async function handleToggleConversation(group: RubikaGroupItem) {
    setError(null);
    try {
      await updateRubikaGroup(group.id, {
        conversation_mode_enabled: !group.conversation_mode_enabled,
      });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    }
  }

  async function handleDelete(group: RubikaGroupItem) {
    setError(null);
    try {
      await deleteRubikaGroup(group.id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    }
  }

  async function handleExpand(group: RubikaGroupItem) {
    if (expandedGroupId === group.id) {
      setExpandedGroupId(null);
      return;
    }
    setExpandedGroupId(group.id);
    setMessagesLoading(true);
    try {
      const result = await fetchRubikaGroupMessages(group.id, { limit: 30 });
      setMessages(result.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("rubikaLoadError"));
    } finally {
      setMessagesLoading(false);
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
          <FormField label={t("rubikaGroupGuid")}>
            <input
              className={inputClassName}
              value={newGuid}
              onChange={(e) => setNewGuid(e.target.value)}
              style={{ direction: "ltr", textAlign: "left" }}
            />
          </FormField>
          <FormField label={t("rubikaGroupName")}>
            <input className={inputClassName} value={newName} onChange={(e) => setNewName(e.target.value)} />
          </FormField>
          <FormField label={t("rubikaGroupKeywords")}>
            <input
              className={inputClassName}
              value={newKeywords}
              onChange={(e) => setNewKeywords(e.target.value)}
              placeholder={t("rubikaGroupKeywordsPlaceholder")}
            />
          </FormField>
          <FormField label={t("rubikaGroupRedKeywords")}>
            <input
              className={inputClassName}
              value={newRedKeywords}
              onChange={(e) => setNewRedKeywords(e.target.value)}
              placeholder={t("rubikaGroupKeywordsPlaceholder")}
            />
          </FormField>
          <FormField label={t("rubikaGroupKeywordResponse")}>
            <textarea
              className={textareaClassName}
              rows={2}
              value={newKeywordResponse}
              onChange={(e) => setNewKeywordResponse(e.target.value)}
            />
          </FormField>
          <div style={{ alignSelf: "end" }}>
            <Button type="submit" disabled={creating}>
              {creating ? t("loading") : t("rubikaGroupAdd")}
            </Button>
          </div>
        </form>
      ) : null}

      {loading && groups.length === 0 ? (
        <EmptyState>{t("loading")}</EmptyState>
      ) : groups.length === 0 ? (
        <EmptyState>{t("rubikaGroupsEmpty")}</EmptyState>
      ) : (
        <TableWrap>
          <table className={tableClassName}>
            <thead>
              <tr>
                <th>{t("rubikaGroupName")}</th>
                <th>{t("rubikaGroupGuid")}</th>
                <th>{t("rubikaGroupKeywords")}</th>
                <th>{t("rubikaGroupConversationMode")}</th>
                <th>{t("rubikaGroupStatus")}</th>
                {canManage ? <th>{t("actions")}</th> : null}
              </tr>
            </thead>
            <tbody>
              {groups.map((group) => (
                <>
                  <tr key={group.id}>
                    <td>
                      <button
                        type="button"
                        onClick={() => void handleExpand(group)}
                        style={{ background: "none", border: "none", cursor: "pointer", fontWeight: 600 }}
                      >
                        {group.group_name ?? t("rubikaGroupUnnamed")}
                      </button>
                    </td>
                    <td style={{ direction: "ltr", textAlign: "left", fontSize: 12 }}>{group.group_guid}</td>
                    <td style={{ fontSize: 12 }}>{group.keywords.join("، ") || "—"}</td>
                    <td>{group.conversation_mode_enabled ? t("yes") : t("no")}</td>
                    <td>{group.is_active ? t("rubikaGroupActive") : t("rubikaGroupInactive")}</td>
                    {canManage ? (
                      <td>
                        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                          <Button type="button" size="sm" onClick={() => void handleToggleActive(group)}>
                            {group.is_active ? t("rubikaGroupDeactivate") : t("rubikaGroupActivate")}
                          </Button>
                          <Button type="button" size="sm" onClick={() => void handleToggleConversation(group)}>
                            {t("rubikaGroupToggleConversation")}
                          </Button>
                          <Button type="button" size="sm" variant="ghost" onClick={() => void handleDelete(group)}>
                            {t("delete")}
                          </Button>
                        </div>
                      </td>
                    ) : null}
                  </tr>
                  {expandedGroupId === group.id ? (
                    <tr key={`${group.id}-messages`}>
                      <td colSpan={canManage ? 6 : 5} style={{ padding: "0 12px 12px" }}>
                        {messagesLoading ? (
                          t("loading")
                        ) : messages.length === 0 ? (
                          <EmptyState>{t("rubikaGroupNoMessages")}</EmptyState>
                        ) : (
                          <div style={{ display: "grid", gap: 6, fontSize: 12, maxHeight: 280, overflowY: "auto" }}>
                            {messages.map((m) => (
                              <div
                                key={m.id}
                                style={{
                                  padding: 8,
                                  borderRadius: 8,
                                  background: m.has_red_keyword ? "rgba(153,27,27,0.08)" : "rgba(0,0,0,0.03)",
                                }}
                              >
                                <div style={{ display: "flex", justifyContent: "space-between", opacity: 0.7 }}>
                                  <span>{m.sender_name ?? "—"}</span>
                                  <span>{toJalaliDateTime(m.received_at)}</span>
                                </div>
                                <div>
                                  {m.message_text ?? m.transcription ?? m.image_extracted_text ?? (
                                    <em>[{m.message_type}]</em>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </td>
                    </tr>
                  ) : null}
                </>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </div>
  );
}
