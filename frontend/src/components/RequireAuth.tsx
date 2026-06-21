import { useRouter } from "next/router";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { useAuth } from "@/state/auth";

export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();
  const router = useRouter();
  const { t } = useTranslation();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      void router.replace("/login");
    }
  }, [isAuthenticated, isLoading, router]);

  if (isLoading) {
    return (
      <div style={{ padding: 48, textAlign: "center", opacity: 0.8 }}>{t("loading")}</div>
    );
  }

  if (!isAuthenticated) {
    return null;
  }

  return <>{children}</>;
}
