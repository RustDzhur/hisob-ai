import type { Metadata } from "next";
import "./globals.css";
import { SessionProvider } from "@/lib/session";

export const metadata: Metadata = { title: "Hisob AI" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}
