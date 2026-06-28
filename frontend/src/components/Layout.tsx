import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui";
import { useAuth } from "@/state/auth";
import { canViewAudit, canViewSettings } from "@/utils/permissions";

type LayoutProps = {
  title: string;
  children: React.ReactNode;
};

function NavLink({ href, label, onNavigate }: { href: string; label: string; onNavigate?: () => void }) {
  const router = useRouter();
  const active = router.pathname === href || router.asPath === href;

  return (
    <Link
      href={href}
      className={`mmp-nav-link${active ? " mmp-nav-link--active" : ""}`}
      onClick={onNavigate}
    >
      {label}
    </Link>
  );
}

export function Layout({ title, children }: LayoutProps) {
  const { t } = useTranslation();
  const router = useRouter();
  const { role, username, isAuthenticated, logout } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    const close = () => setSidebarOpen(false);
    router.events.on("routeChangeComplete", close);
    return () => {
      router.events.off("routeChangeComplete", close);
    };
  }, [router.events]);

  const navItems = (
    <nav className="app-nav">
      <NavLink href="/" label={t("dashboard")} onNavigate={() => setSidebarOpen(false)} />
      <NavLink href="/accounts" label={t("accounts")} onNavigate={() => setSidebarOpen(false)} />
      <NavLink href="/contacts" label={t("contacts")} onNavigate={() => setSidebarOpen(false)} />
      <NavLink href="/campaigns" label={t("campaigns")} onNavigate={() => setSidebarOpen(false)} />
      <NavLink
        href="/reports/messages"
        label={t("messageLogs")}
        onNavigate={() => setSidebarOpen(false)}
      />
      {canViewAudit(role) ? (
        <NavLink
          href="/reports/audit"
          label={t("auditLogs")}
          onNavigate={() => setSidebarOpen(false)}
        />
      ) : null}
      {canViewSettings(role) ? (
        <NavLink href="/settings" label={t("settings")} onNavigate={() => setSidebarOpen(false)} />
      ) : null}
    </nav>
  );

  return (
    <RequireAuth>
      <div className="app-shell">
        <div
          className={`app-overlay${sidebarOpen ? " app-overlay--visible" : ""}`}
          role="presentation"
          onClick={() => setSidebarOpen(false)}
          onKeyDown={() => undefined}
        />

        <div className="app-main">
          <header className="app-topbar">
            <div>
              <div className="app-topbar__title">{title}</div>
              <div className="app-topbar__meta">
                {isAuthenticated ? `${username ?? "—"} · ${t(role)}` : t("notSignedIn")}
              </div>
            </div>
            <div className="app-topbar__actions">
              <Button
                type="button"
                className="app-menu-btn"
                aria-expanded={sidebarOpen}
                aria-label={t("openMenu")}
                onClick={() => setSidebarOpen((v) => !v)}
              >
                ☰
              </Button>
              <Link href="/login" className="mmp-link-muted">
                {t("login")}
              </Link>
              <Button
                type="button"
                size="sm"
                onClick={() => {
                  logout();
                  void router.push("/login");
                }}
              >
                {t("logout")}
              </Button>
            </div>
          </header>

          {children}
        </div>

        <aside className={`app-sidebar${sidebarOpen ? " app-sidebar--open" : ""}`}>
          <div className="app-brand">{t("senderPlatform")}</div>
          {navItems}
        </aside>
      </div>
    </RequireAuth>
  );
}
