import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { useT } from "@/i18n/LocaleProvider";

export function AuthLayout({ title, children }: { title: string; children: React.ReactNode }) {
  const t = useT();
  return (
    <main className="auth-wrap">
      <div className="card auth-card">
        <div className="auth-top">
          <LanguageSwitcher />
        </div>
        <div className="brand">
          <span className="brand-dot" aria-hidden />
          {t.brand}
        </div>
        <h1>{title}</h1>
        <div style={{ height: 16 }} />
        {children}
      </div>
    </main>
  );
}
