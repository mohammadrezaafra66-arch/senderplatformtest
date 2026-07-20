import Head from "next/head";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Layout } from "@/components/Layout";
import { RubikaAssistantCard } from "@/components/RubikaAssistantCard";
import { RubikaContentScheduleManager } from "@/components/RubikaContentScheduleManager";
import { RubikaGroupManager } from "@/components/RubikaGroupManager";
import { RubikaPoolPanel } from "@/components/RubikaPoolPanel";
import { RubikaSendLogPanel } from "@/components/RubikaSendLogPanel";
import { Panel, PageContent } from "@/components/ui";
import { useAuth } from "@/state/auth";
import { canManageRubika } from "@/utils/permissions";

type Tab = "pool" | "groups" | "send-log" | "assistant" | "status";

export default function RubikaPage() {
  const { t } = useTranslation();
  const { role } = useAuth();
  const canManage = canManageRubika(role);
  const [tab, setTab] = useState<Tab>("pool");

  const tabs: { key: Tab; label: string }[] = [
    { key: "pool", label: t("rubikaTabPool") },
    { key: "groups", label: t("rubikaTabGroups") },
    { key: "send-log", label: t("rubikaTabSendLog") },
    { key: "assistant", label: t("rubikaTabAssistant") },
    { key: "status", label: t("rubikaTabStatus") },
  ];

  return (
    <Layout title={t("rubikaPageTitle")}>
      <Head>
        <title>{t("rubikaPageTitle")}</title>
      </Head>
      <PageContent>
        <Panel
          title={t("rubikaPageTitle")}
          headerExtra={
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {tabs.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => setTab(item.key)}
                  style={{
                    padding: "6px 12px",
                    borderRadius: 8,
                    border: "1px solid rgba(0,0,0,0.12)",
                    background: tab === item.key ? "rgba(29,78,216,0.12)" : "transparent",
                    cursor: "pointer",
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          }
        >
          <div style={{ padding: 16 }}>
            {tab === "pool" ? <RubikaPoolPanel canManage={canManage} /> : null}
            {tab === "groups" ? <RubikaGroupManager canManage={canManage} /> : null}
            {tab === "send-log" ? <RubikaSendLogPanel /> : null}
            {tab === "assistant" ? <RubikaAssistantCard /> : null}
            {tab === "status" ? <RubikaContentScheduleManager canManage={canManage} /> : null}
          </div>
        </Panel>
      </PageContent>
    </Layout>
  );
}
