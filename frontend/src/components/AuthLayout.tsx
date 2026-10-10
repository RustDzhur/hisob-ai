import { ru } from "@/i18n/ru";

export function AuthLayout({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main className="auth-wrap">
      <div className="card auth-card">
        <div className="brand">
          <span className="brand-dot" aria-hidden />
          {ru.brand}
        </div>
        <h1>{title}</h1>
        <div style={{ height: 16 }} />
        {children}
      </div>
    </main>
  );
}
