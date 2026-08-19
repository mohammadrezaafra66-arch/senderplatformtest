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
import {
  createCampaignFromImport,
  createCampaignFromContacts,
  fetchGptStatus,
  fetchProductFeedStatus,
  previewCampaignRender,
  previewGptVariations,
} from "@/lib/campaign-api";
import { searchContacts } from "@/lib/contacts-api";
import { useAuth } from "@/state/auth";
import type { CampaignRenderPreviewSample, PlatformOption } from "@/types/campaign";
import type { AccountItem } from "@/types/account";
import type { ContactSearchItem } from "@/types/contacts";
import { canCreateCampaign } from "@/utils/permissions";

export default function CampaignCreatePage() {
  const { t } = useTranslation();
  const router = useRouter();
  const { role } = useAuth();
  const canCreate = canCreateCampaign(role);

  const [importBatchId, setImportBatchId] = useState("");
  const [recipientSource, setRecipientSource] = useState<"import" | "existing">("import");

  const [contactsQuery, setContactsQuery] = useState("");
  const [contactsLoading, setContactsLoading] = useState(false);
  const [contactsError, setContactsError] = useState<string | null>(null);
  const [contacts, setContacts] = useState<ContactSearchItem[]>([]);
  const [selectedContactIds, setSelectedContactIds] = useState<number[]>([]);

  const [title, setTitle] = useState("");
  const [platform, setPlatform] = useState<PlatformOption>("bale");
  const [templateText, setTemplateText] = useState("سلام {{first_name}}، پیام تست کمپین.");
  const [useGpt, setUseGpt] = useState(false);
  const [includeProducts, setIncludeProducts] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedStatus, setFeedStatus] = useState<string | null>(null);
  const [feedChecking, setFeedChecking] = useState(false);
  const [gptStatus, setGptStatus] = useState<string | null>(null);
  const [gptPreviewing, setGptPreviewing] = useState(false);
  const [gptPreviewError, setGptPreviewError] = useState<string | null>(null);
  const [gptSamples, setGptSamples] = useState<
    { title: string; prose: string; products: string; finalText: string; note?: string }[]
  >([]);
  const [gptProductNote, setGptProductNote] = useState<string | null>(null);
  const [samplePreviewing, setSamplePreviewing] = useState(false);
  const [samplePreviewError, setSamplePreviewError] = useState<string | null>(null);
  const [samplePreviews, setSamplePreviews] = useState<CampaignRenderPreviewSample[]>([]);
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

  const searchExistingContacts = useCallback(async () => {
    const q = contactsQuery.trim();
    if (!q) {
      setContacts([]);
      setContactsError(null);
      return;
    }
    setContactsLoading(true);
    setContactsError(null);
    try {
      const result = await searchContacts({ q, limit: 50, offset: 0 });
      setContacts(result.items);
    } catch (err) {
      setContactsError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setContactsLoading(false);
    }
  }, [contactsQuery, t]);

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

    if (!title.trim() || !templateText.trim()) {
      setError(t("requiredFields"));
      return;
    }
    if (senderMode === "manual" && selectedAccountIds.length === 0) {
      setError(t("senderManualRequired"));
      return;
    }

    if (recipientSource === "import") {
      const batchId = Number.parseInt(importBatchId, 10);
      if (!Number.isFinite(batchId) || batchId < 1) {
        setError(t("invalidImportBatchId"));
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
      return;
    }

    if (selectedContactIds.length === 0) {
      setError("حداقل یک مخاطب را انتخاب کنید.");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const result = await createCampaignFromContacts({
        contact_ids: selectedContactIds,
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

              <FormField label="منبع گیرنده">
                <select
                  className={selectClassName}
                  value={recipientSource}
                  onChange={(e) => {
                    const next = e.target.value as "import" | "existing";
                    setRecipientSource(next);
                    setSelectedContactIds([]);
                    setContacts([]);
                    setContactsQuery("");
                    setContactsError(null);
                    setError(null);
                  }}
                  disabled={submitting}
                >
                  <option value="import">از فایل / دسته import</option>
                  <option value="existing">از مخاطبان موجود</option>
                </select>
              </FormField>

              {recipientSource === "import" ? (
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
              ) : (
                <>
                  <FormField label="جستجوی مخاطب">
                    <input
                      className={inputClassName}
                      value={contactsQuery}
                      onChange={(e) => setContactsQuery(e.target.value)}
                      placeholder="نام یا شماره تلفن..."
                      disabled={submitting}
                    />
                  </FormField>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <Button
                      type="button"
                      disabled={submitting || contactsLoading || !contactsQuery.trim()}
                      onClick={() => void searchExistingContacts()}
                    >
                      {t("search")}
                    </Button>
                    <div className="mmp-muted" style={{ fontSize: 13 }}>
                      انتخاب‌شده: <strong>{selectedContactIds.length}</strong>
                    </div>
                  </div>
                  {contactsError ? (
                    <p style={{ color: "crimson", margin: 0 }}>{contactsError}</p>
                  ) : null}

                  <div style={{ marginTop: 12, display: "grid", gap: 8 }}>
                    {contactsLoading ? <p className="mmp-muted">در حال جستجو...</p> : null}

                    {contacts.length === 0 && !contactsLoading ? (
                      <p className="mmp-muted">نتیجه‌ای یافت نشد.</p>
                    ) : null}

                    {contacts.map((c) => {
                      const selected = selectedContactIds.includes(c.contact_id);
                      const displayName =
                        c.full_name ||
                        [c.first_name, c.last_name].filter(Boolean).join(" ") ||
                        `#${c.contact_id}`;
                      return (
                        <label
                          key={c.contact_id}
                          style={{
                            display: "flex",
                            gap: 10,
                            alignItems: "flex-start",
                            opacity: c.eligible ? 1 : 0.55,
                            border: "1px solid rgba(0,0,0,0.08)",
                            borderRadius: 10,
                            padding: "10px 12px",
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={selected}
                            disabled={!c.eligible || submitting}
                            onChange={() => {
                              setSelectedContactIds((prev) => {
                                if (prev.includes(c.contact_id)) {
                                  return prev.filter((id) => id !== c.contact_id);
                                }
                                return [...prev, c.contact_id];
                              });
                            }}
                          />
                          <div style={{ display: "grid", gap: 4 }}>
                            <div style={{ fontWeight: 700 }}>{displayName}</div>
                            <div className="mmp-muted" style={{ fontSize: 13 }}>
                              {c.phone}
                            </div>
                            {!c.eligible ? (
                              <div
                                className="mmp-muted"
                                style={{ fontSize: 13, color: "crimson" }}
                              >
                                {c.ineligible_reason}
                              </div>
                            ) : null}
                          </div>
                        </label>
                      );
                    })}
                  </div>
                </>
              )}

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
                  onChange={(e) => {
                    const enabled = e.target.checked;
                    setUseGpt(enabled);
                    if (!enabled) {
                      setGptSamples([]);
                      setGptPreviewError(null);
                      setGptProductNote(null);
                    } else {
                      setGptStatus(null);
                      void fetchGptStatus()
                        .then((result) => setGptStatus(result.message))
                        .catch(() => setGptStatus(t("gptNotConfigured")));
                    }
                  }}
                />
                <span>{t("useGpt")}</span>
              </label>
              {useGpt ? (
                <div className="mmp-stack" style={{ gap: 8 }}>
                  <p className="mmp-muted">{t("gptPreviewSection")}</p>
                  {gptStatus ? <p className="mmp-muted">{gptStatus}</p> : null}
                  <Button
                    type="button"
                    disabled={gptPreviewing || submitting || !templateText.trim()}
                    onClick={() => {
                      setGptPreviewing(true);
                      setGptPreviewError(null);
                      void previewGptVariations({
                        template_text: templateText.trim(),
                        include_products: includeProducts,
                        requested_count: 3,
                      })
                        .then((result) => {
                          setGptProductNote(result.product_preview_note || null);
                          if (!result.ok && result.message) {
                            setGptPreviewError(result.message);
                          }
                          setGptSamples(
                            (result.samples || []).map((sample, index) => ({
                              title: t("gptPreviewSample", { n: index + 1 }),
                              prose: sample.prose_text,
                              products: sample.immutable_product_block,
                              finalText: sample.final_text,
                            })),
                          );
                        })
                        .catch((err) => {
                          setGptSamples([]);
                          setGptPreviewError(
                            err instanceof ApiError ? err.message : t("gptPreviewFailed"),
                          );
                        })
                        .finally(() => setGptPreviewing(false));
                    }}
                  >
                    {gptPreviewing
                      ? t("loading")
                      : gptSamples.length
                        ? t("gptPreviewRegenerate")
                        : t("gptPreviewGenerate")}
                  </Button>
                  {gptPreviewError ? <Alert>{gptPreviewError}</Alert> : null}
                  {gptSamples.length ? (
                    <div className="mmp-stack" style={{ gap: 12 }}>
                      <p>{t("gptPreviewTitle")}</p>
                      {gptProductNote ? <p className="mmp-muted">{gptProductNote}</p> : null}
                      {gptSamples.map((sample) => (
                        <Panel key={sample.title}>
                          <PanelContent>
                            <strong>{sample.title}</strong>
                            <p style={{ whiteSpace: "pre-wrap" }}>{sample.finalText}</p>
                          </PanelContent>
                        </Panel>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}

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

              <div className="mmp-stack" style={{ gap: 8 }}>
                <p className="mmp-muted">{t("samplePreviewHint")}</p>
                <Button
                  type="button"
                  disabled={samplePreviewing || submitting || !templateText.trim()}
                  onClick={() => {
                    setSamplePreviewing(true);
                    setSamplePreviewError(null);
                    void previewCampaignRender({
                      template_text: templateText.trim(),
                      platform,
                      use_gpt: useGpt,
                      include_products: includeProducts,
                      preview_count: 3,
                    })
                      .then((result) => {
                        if (!result.ok && result.message) {
                          setSamplePreviewError(result.message);
                        }
                        setSamplePreviews(result.samples || []);
                      })
                      .catch((err) => {
                        setSamplePreviews([]);
                        setSamplePreviewError(
                          err instanceof ApiError ? err.message : t("samplePreviewFailed"),
                        );
                      })
                      .finally(() => setSamplePreviewing(false));
                  }}
                >
                  {samplePreviewing ? t("loading") : t("samplePreviewGenerate")}
                </Button>
                {samplePreviewError ? <Alert>{samplePreviewError}</Alert> : null}
                {samplePreviews.length ? (
                  <div className="mmp-stack" style={{ gap: 12 }}>
                    <p>
                      <strong>{t("samplePreviewLabel")}</strong>
                    </p>
                    {samplePreviews.map((sample, index) => (
                      <Panel key={`${sample.render_batch_id}-${index}`}>
                        <PanelContent>
                          <strong>
                            {t("gptPreviewSample", { n: index + 1 })} — {t("samplePreviewLabel")}
                          </strong>
                          <p className="mmp-muted">{sample.sample_warning}</p>
                          <p style={{ whiteSpace: "pre-wrap" }}>{sample.final_text}</p>
                          <p className="mmp-muted">
                            {t("useGpt")}: {sample.use_gpt ? t("yes") : t("no")}
                            {sample.variation_id ? ` — ${t("gptVariationId")}: ${sample.variation_id}` : ""}
                          </p>
                          <p className="mmp-muted">
                            {t("includeProducts")}: {sample.include_products ? t("yes") : t("no")}
                            {sample.include_products ? ` (${sample.product_count})` : ""}
                          </p>
                        </PanelContent>
                      </Panel>
                    ))}
                  </div>
                ) : null}
              </div>

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
