import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Layout } from "@/components/Layout";
import { CampaignSenderSelector, type SenderMode } from "@/components/CampaignSenderSelector";
import {
  Alert,
  Button,
  FormField,
  PageContent,
  Panel,
  PanelContent,
  inputClassName,
  selectClassName,
  textareaClassName,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import { fetchAccounts } from "@/lib/accounts-api";
import { campaignAccountError } from "@/lib/campaign-account-errors";
import { createCampaignFromImport, fetchProductFeedStatus } from "@/lib/campaign-api";
import { useAuth } from "@/state/auth";
import type { PlatformOption } from "@/types/campaign";
import type { AccountItem } from "@/types/account";
import { canCreateCampaign } from "@/utils/permissions";

export default function CampaignCreatePage() {
  const { t } = useTranslation();
  const router = useRouter();
  const { role } = useAuth();
  const canCreate = canCreateCampaign(role);

  const [importBatchId, setImportBatchId] = useState("");
  const [title, setTitle] = useState("");
  const [platform, setPlatform] = useState<PlatformOption>("bale");
  const [templateText, setTemplateText] = useState("سلام {{first_name}}، پیام تست کمپین.");
  const [useGpt, setUseGpt] = useState(false);
  const [includeProducts, setIncludeProducts] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedStatus, setFeedStatus] = useState<string | null>(null);
  const [feedChecking, setFeedChecking] = useState(false);
  const [accounts, setAccounts] = useState<AccountItem[]>([]);
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const [senderMode, setSenderMode] = useState<SenderMode>("auto");
  const [selectedAccountIds, setSelectedAccountIds] = useState<number[]>([]);

  const loadAccounts = useCallback(async () => {
    setAccountsLoading(true);
    setAccountsError(null);
    try {
      const result = await fetchAccounts();
      setAccounts(result.items);
    } catch (err) {
      setAccountsError(err instanceof ApiError ? err.message : t("accountsLoadError"));
    } finally {
      setAccountsLoading(false);
    }
  }, [t]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial remote data load
    void loadAccounts();
  }, [loadAccounts]);

  useEffect(() => {
    if (!router.isReady) return;
    const raw = router.query.import_batch_id;
    if (typeof raw === "string" && raw.trim()) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- synchronize the form with the query parameter
      setImportBatchId(raw.trim());
    }
  }, [router.isReady, router.query.import_batch_id]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canCreate) return;

    const batchId = Number.parseInt(importBatchId, 10);
    if (!Number.isFinite(batchId) || batchId < 1) {
      setError(t("invalidImportBatchId"));
      return;
    }
    if (!title.trim() || !templateText.trim()) {
      setError(t("requiredFields"));
      return;
    }
    if (senderMode === "manual" && selectedAccountIds.length === 0) {
      setError(t("senderManualRequired"));
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const result = await createCampaignFromImport({
        import_batch_id: batchId,
        title: title.trim(),
        platform,
        template_text: templateText.trim(),
        use_gpt: useGpt,
        include_products: includeProducts,
        account_ids: senderMode === "auto" ? [] : selectedAccountIds,
      });
      void router.push(`/campaigns/${result.campaign_id}`);
    } catch (err) {
      setError(campaignAccountError(err, t));
      setSubmitting(false);
    }
  }

  return (
    <>
      <Head>
        <title>{t("createCampaign")}</title>
      </Head>
      <Layout title={t("createCampaign")}>
        <PageContent style={{ maxWidth: 560 }}>
          {!canCreate ? (
            <Panel>
              <PanelContent>{t("notAllowed")}</PanelContent>
            </Panel>
          ) : (
            <form className="mmp-form-grid" onSubmit={(e) => void handleSubmit(e)}>
              <p className="mmp-muted">{t("createCampaignHint")}</p>

              <FormField label={t("importBatchId")}>
                <input
                  type="number"
                  min={1}
                  className={inputClassName}
                  value={importBatchId}
                  onChange={(e) => setImportBatchId(e.target.value)}
                  required
                />
              </FormField>

              <FormField label={t("campaignTitle")}>
                <input
                  className={inputClassName}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  required
                />
              </FormField>

              <FormField label={t("platform")}>
                <select
                  className={selectClassName}
                  value={platform}
                  onChange={(e) => {
                    setPlatform(e.target.value as PlatformOption);
                    setSelectedAccountIds([]);
                  }}
                >
                  <option value="bale">bale</option>
                  <option value="telegram">telegram</option>
                  <option value="whatsapp">whatsapp</option>
                  <option value="rubika">rubika</option>
                </select>
              </FormField>

              <CampaignSenderSelector
                platform={platform}
                accounts={accounts}
                mode={senderMode}
                selectedIds={selectedAccountIds}
                loading={accountsLoading}
                loadError={accountsError}
                disabled={submitting}
                onModeChange={setSenderMode}
                onSelectedIdsChange={setSelectedAccountIds}
                onRetry={() => void loadAccounts()}
              />

              <FormField label={t("templateText")}>
                <textarea
                  className={textareaClassName}
                  value={templateText}
                  onChange={(e) => setTemplateText(e.target.value)}
                  rows={4}
                  required
                />
              </FormField>

              <label className="mmp-stack">
                <input
                  type="checkbox"
                  checked={useGpt}
                  onChange={(e) => setUseGpt(e.target.checked)}
                />
                <span>{t("useGpt")}</span>
              </label>

              <label className="mmp-stack">
                <input
                  type="checkbox"
                  checked={includeProducts}
                  onChange={(e) => setIncludeProducts(e.target.checked)}
                />
                <span>{t("includeProducts")}</span>
              </label>
              <p className="mmp-muted">{t("includeProductsHint")}</p>
              {includeProducts ? (
                <div className="mmp-stack" style={{ gap: 8 }}>
                  <Button
                    type="button"
                    disabled={feedChecking || submitting}
                    onClick={() => {
                      setFeedChecking(true);
                      setFeedStatus(null);
                      void fetchProductFeedStatus()
                        .then((result) => {
                          setFeedStatus(
                            result.ok
                              ? t("productFeedReady", { count: result.eligible_count })
                              : result.message || t("productFeedFailed"),
                          );
                        })
                        .catch((err) => {
                          setFeedStatus(
                            err instanceof ApiError ? err.message : t("productFeedFailed"),
                          );
                        })
                        .finally(() => setFeedChecking(false));
                    }}
                  >
                    {feedChecking ? t("loading") : t("productFeedCheck")}
                  </Button>
                  {feedStatus ? <p className="mmp-muted">{feedStatus}</p> : null}
                </div>
              ) : null}

              {error ? <Alert>{error}</Alert> : null}

              <Button
                type="submit"
                variant="primary"
                disabled={submitting || (senderMode === "manual" && selectedAccountIds.length === 0)}
              >
                {submitting ? t("loading") : t("createCampaign")}
              </Button>
            </form>
          )}

          <div style={{ marginTop: 16 }}>
            <Link href="/campaigns" className="mmp-link-muted">
              {t("backToCampaigns")}
            </Link>
          </div>
        </PageContent>
      </Layout>
    </>
  );
}
