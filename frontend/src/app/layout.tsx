import type { Metadata } from "next";
import { cookies } from "next/headers";
import "./globals.css";
import { DEFAULT_LOCALE, isLocale, LANG_COOKIE } from "@/i18n";
import { LocaleProvider } from "@/i18n/LocaleProvider";
import { SessionProvider } from "@/lib/session";

export const metadata: Metadata = { title: "Hisob AI" };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const fromCookie = (await cookies()).get(LANG_COOKIE)?.value;
  const locale = isLocale(fromCookie) ? fromCookie : DEFAULT_LOCALE;
  return (
    <html lang={locale}>
      <body>
        <LocaleProvider initial={locale}>
          <SessionProvider>{children}</SessionProvider>
        </LocaleProvider>
      </body>
    </html>
  );
}
