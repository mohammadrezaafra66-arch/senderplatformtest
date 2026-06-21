import Head from "next/head";
import { useTranslation } from "react-i18next";

import { Layout } from "@/components/Layout";
import { WhatsAppWarmupTrigger } from "@/components/WhatsAppWarmupTrigger";
import { EmptyState, PageContent, Panel, PanelContent } from "@/components/ui";
import { useAuth } from "@/state/auth";
import { canTriggerWhatsAppWarmup, canViewSettings } from "@/utils/permissions";

export default function SettingsPage() {
  const { t } = useTranslation();
  const { role } = useAuth();
  const canView = canViewSettings(role);
  const canWarmup = canTriggerWhatsAppWarmup(role);

  return (
    <>
      <Head>
        <title>{t("settings")}</title>
      </Head>
      <Layout title={t("settings")}>
        <PageContent>
          {!canView ? (
            <Panel>
              <PanelContent>{t("notAllowed")}</PanelContent>
            </Panel>
          ) : (
            <>
              {canWarmup ? (
                <Panel title={t("warmupPanelTitle")}>
                  <PanelContent>
                    <WhatsAppWarmupTrigger />
                  </PanelContent>
                </Panel>
              ) : null}

              <Panel title={t("settingsAdminOnly")}>
                <PanelContent>
                  <p className="mmp-muted">{t("settingsPlaceholder")}</p>
                  <ul className="mmp-muted" style={{ marginTop: 12, paddingRight: 18 }}>
                    <li>{t("settingsRateCaps")}</li>
                    <li>{t("settingsRbac")}</li>
                    <li>{t("settingsSecrets")}</li>
                  </ul>
                  <EmptyState>{t("settingsComingSoon")}</EmptyState>
                </PanelContent>
              </Panel>
            </>
          )}
        </PageContent>
      </Layout>
    </>
  );
}
