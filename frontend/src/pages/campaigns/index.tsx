import Head from "next/head";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Layout } from "@/components/Layout";
import {
  Alert,
  Button,
  EmptyState,
  FormField,
  PageContent,
  Panel,
  TableWrap,
  selectClassName,
  tableClassName,
} from "@/components/ui";
import { fetchCampaigns } from "@/lib/campaign-api";
import { useAuth } from "@/state/auth";
import type { CampaignListItem } from "@/types/campaign";
import { campaignStatusLabel } from "@/utils/campaign-status";
import { canCreateCampaign, canViewCampaigns } from "@/utils/permissions";

export default function CampaignsListPage() {
  const { t } = useTranslation();
  const { role } = useAuth();
  const canCreate = canCreateCampaign(role);
  const canView = canViewCampaigns(role);

  const [items, setItems] = useState<CampaignListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [statusFilter, setStatusFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const limit = 20;

  useEffect(() => {
    if (!canView) {
      setLoading(false);
      return;
    }

    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchCampaigns({
          limit,
          offset,
          status: statusFilter || undefined,
        });
        if (!cancelled) {
          setItems(data.items);
          setTotal(data.total_count);
        }
      } catch {
        if (!cancelled) setError(t("campaignsLoadError"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [canView, offset, statusFilter, t]);

  return (
    <>
      <Head>
        <title>{t("campaigns")}</title>
      </Head>
      <Layout title={t("campaigns")}>
        <PageContent>
          {!canView ? (
            <Panel>
              <EmptyState>{t("notAllowed")}</EmptyState>
            </Panel>
          ) : (
            <>
              <div className="mmp-stack">
                {canCreate ? (
                  <Link href="/campaigns/create" className="mmp-btn mmp-btn--primary">
                    {t("createCampaign")}
                  </Link>
                ) : (
                  <span className="mmp-btn" style={{ opacity: 0.6 }}>
                    {t("createCampaign")}
                  </span>
                )}
                <FormField label={t("status")}>
                  <select
                    className={selectClassName}
                    style={{ width: "auto", minWidth: 160 }}
                    value={statusFilter}
                    onChange={(e) => {
                      setOffset(0);
                      setStatusFilter(e.target.value);
                    }}
                  >
                    <option value="">{t("allStatuses")}</option>
                    <option value="draft">{t("status_draft")}</option>
                    <option value="prepared">{t("status_prepared")}</option>
                    <option value="running">{t("status_running")}</option>
                    <option value="paused">{t("status_paused")}</option>
                  </select>
                </FormField>
              </div>

              {error ? <Alert>{error}</Alert> : null}

              <Panel title={`${t("campaigns")} (${total})`} flushTable>
                {loading ? (
                  <EmptyState>{t("loading")}</EmptyState>
                ) : items.length === 0 ? (
                  <EmptyState>{t("noCampaigns")}</EmptyState>
                ) : (
                  <TableWrap>
                    <table className={tableClassName}>
                      <thead>
                        <tr>
                          <th>#</th>
                          <th>{t("campaignTitle")}</th>
                          <th>{t("platform")}</th>
                          <th>{t("status")}</th>
                          <th>{t("recipients")}</th>
                          <th>{t("actions")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {items.map((c) => (
                          <tr key={c.id}>
                            <td>{c.id}</td>
                            <td>{c.title}</td>
                            <td>{c.platform}</td>
                            <td>{campaignStatusLabel(c.status, t)}</td>
                            <td>{c.total_recipients}</td>
                            <td>
                              <Link href={`/campaigns/${c.id}`}>{t("monitor")}</Link>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </TableWrap>
                )}
              </Panel>

              <div className="mmp-stack" style={{ marginTop: 12 }}>
                <Button
                  type="button"
                  size="sm"
                  disabled={offset <= 0}
                  onClick={() => setOffset((o) => Math.max(0, o - limit))}
                >
                  {t("prevPage")}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  disabled={offset + limit >= total}
                  onClick={() => setOffset((o) => o + limit)}
                >
                  {t("nextPage")}
                </Button>
              </div>
            </>
          )}
        </PageContent>
      </Layout>
    </>
  );
}
