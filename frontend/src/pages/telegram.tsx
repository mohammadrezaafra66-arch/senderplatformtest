import Head from "next/head";
import { useTranslation } from "react-i18next";

import { Layout } from "@/components/Layout";
import { TelegramAccountPool } from "@/components/TelegramAccountPool";
import { TelegramAccountSetup } from "@/components/TelegramAccountSetup";
import { TelegramLeadsPanel } from "@/components/TelegramLeadsPanel";
import { TelegramScheduleForm } from "@/components/TelegramScheduleForm";
import { PageContent, Panel, PanelContent } from "@/components/ui";

export default function TelegramPage() {
  const { t } = useTranslation();
  const pageTitle = t("telegramPageTitle", { defaultValue: "تلگرام MTProto" });

  return (
    <>
      <Head>
        <title>{pageTitle}</title>
      </Head>
      {/* Layout خودش RequireAuth را رندر می‌کند؛ اینجا دوباره اضافه نمی‌شود. */}
      <Layout title={pageTitle}>
        <PageContent>
          <Panel title={t("telegramScheduleTitle", { defaultValue: "بازه زمانی ارسال" })}>
            <PanelContent>
              <TelegramScheduleForm />
            </PanelContent>
          </Panel>

          <Panel
            title={t("telegramPoolTitle", { defaultValue: "استخر اکانت‌های تلگرام" })}
            flushTable
          >
            <TelegramAccountPool />
          </Panel>

          <Panel title={t("telegramLeadsTitle", { defaultValue: "لیدهای تلگرام" })} flushTable>
            <TelegramLeadsPanel />
          </Panel>

          <Panel title={t("telegramSetupTitle", { defaultValue: "ورود با شماره تلفن" })}>
            <PanelContent>
              <TelegramAccountSetup />
            </PanelContent>
          </Panel>
        </PageContent>
      </Layout>
    </>
  );
}
