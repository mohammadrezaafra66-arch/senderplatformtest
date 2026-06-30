import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, Button } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { fetchAfrakalaAssistantPricing, refreshAfrakalaAssistantPricing } from "@/lib/rubika-api";
import type { RubikaAssistantPricing } from "@/lib/rubika-api";
import { toJalaliDateTime } from "@/utils/jalali";

export function RubikaAssistantCard() {
  const { t } = useTranslation();
  const [pricing, setPricing] = useState<RubikaAssistantPricing | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchAfrakalaAssistantPricing();
      setPricing(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("rubikaLoadError"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleRefresh() {
    setRefreshing(true);
    setError(null);
    try {
      const result = await refreshAfrakalaAssistantPricing();
      setPricing(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div
      style={{
        border: "1px solid rgba(0,0,0,0.1)",
        borderRadius: 10,
        padding: 12,
        display: "grid",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <strong>{t("rubikaAssistantTitle")}</strong>
        <Button type="button" size="sm" onClick={() => void handleRefresh()} disabled={refreshing}>
          {refreshing ? t("loading") : t("rubikaAssistantRefresh")}
        </Button>
      </div>

      {error ? <Alert variant="error">{error}</Alert> : null}

      {loading ? (
        <div>{t("loading")}</div>
      ) : pricing ? (
        <div style={{ fontSize: 12, display: "grid", gap: 4 }}>
          {pricing.cached_at ? (
            <div>
              {t("rubikaAssistantCachedAt")}: {toJalaliDateTime(pricing.cached_at)}
            </div>
          ) : null}
          <pre
            style={{
              margin: 0,
              padding: 8,
              borderRadius: 8,
              background: "rgba(0,0,0,0.04)",
              maxHeight: 220,
              overflow: "auto",
              direction: "ltr",
              textAlign: "left",
            }}
          >
            {JSON.stringify(pricing, null, 2)}
          </pre>
        </div>
      ) : (
        <div>{t("rubikaAssistantEmpty")}</div>
      )}
    </div>
  );
}
