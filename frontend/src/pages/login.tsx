import Head from "next/head";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, Button, FormField, inputClassName } from "@/components/ui";
import { getLoginErrorMessage, useAuth } from "@/state/auth";

export default function LoginPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const { login, isAuthenticated, isLoading } = useAuth();
  const [username, setUsername] = useState("operator");
  const [password, setPassword] = useState("operator123");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isLoading && isAuthenticated) {
      void router.replace("/");
    }
  }, [isAuthenticated, isLoading, router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username.trim(), password);
      void router.push("/");
    } catch (err) {
      setError(getLoginErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Head>
        <title>{t("login")}</title>
        <meta name="description" content="Login" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </Head>
      <main className="mmp-login">
        <h1 className="mmp-login__title">{t("login")}</h1>
        <form className="mmp-form-grid" onSubmit={(e) => void handleSubmit(e)}>
          <FormField label={t("username")}>
            <input
              name="username"
              autoComplete="username"
              className={inputClassName}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </FormField>
          <FormField label={t("password")}>
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              className={inputClassName}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </FormField>
          {error ? <Alert>{error}</Alert> : null}
          <Button type="submit" variant="primary" disabled={submitting}>
            {submitting ? t("loading") : t("signIn")}
          </Button>
        </form>
        <p className="mmp-hint">admin / admin123 — operator / operator123 — viewer / viewer123</p>
      </main>
    </>
  );
}
