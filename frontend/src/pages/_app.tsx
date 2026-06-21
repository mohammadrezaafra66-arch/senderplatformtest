import "@/styles/globals.css";
import type { AppProps } from "next/app";

import "@/i18n";
import { AuthProvider } from "@/state/auth";

export default function App({ Component, pageProps }: AppProps) {
  return (
    <AuthProvider>
      <Component {...pageProps} />
    </AuthProvider>
  );
}
