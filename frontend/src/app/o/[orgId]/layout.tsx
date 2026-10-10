"use client";

import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { Button, ErrorState, Skeleton } from "@/components/ui";
import { useT } from "@/i18n/LocaleProvider";
import { rememberOrg, useSession } from "@/lib/session";

export default function OrgLayout({ children }: { children: React.ReactNode }) {
  const t = useT();
  const { orgId } = useParams<{ orgId: string }>();
  const router = useRouter();
  const pathname = usePathname();
  const { state, reload, logout } = useSession();

  useEffect(() => {
    if (state.status === "anon") router.replace("/login");
  }, [state, router]);

  useEffect(() => {
    if (state.status === "ready" && state.orgs.some((o) => o.id === orgId)) rememberOrg(orgId);
  }, [state, orgId]);

  if (state.status === "error") return <main className="content"><ErrorState error={state.error} onRetry={reload} /></main>;
  if (state.status !== "ready") return <main className="content"><Skeleton /></main>;

  const { user, orgs } = state;
  const NEW_ORG = "__new__";
  const current = orgs.find((o) => o.id === orgId);
  const home = `/o/${orgId}`;
  const membersHref = `${home}/members`;
  const auditHref = `${home}/audit`;
  const accountHref = `${home}/account`;
  const currenciesHref = `${home}/currencies`;
  const unitsHref = `${home}/units`;
  const counterpartiesHref = `${home}/counterparties`;
  const productsHref = `${home}/products`;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-dot" aria-hidden />
          {t.brand}
        </div>
        <nav className="nav" aria-label={t.shell.navLabel}>
          <Link href={home} aria-current={pathname === home ? "page" : undefined}>{t.shell.home}</Link>
          {current?.permissions.includes("members.read") ? (
            <Link href={membersHref} aria-current={pathname === membersHref ? "page" : undefined}>{t.members.navLabel}</Link>
          ) : null}
          {current?.permissions.includes("catalog.read") ? (
            <>
              <div className="nav-section">{t.catalog.navSection}</div>
              <Link href={counterpartiesHref} aria-current={pathname === counterpartiesHref ? "page" : undefined}>{t.counterparties.navLabel}</Link>
              <Link href={productsHref} aria-current={pathname === productsHref ? "page" : undefined}>{t.products.navLabel}</Link>
              <Link href={currenciesHref} aria-current={pathname === currenciesHref ? "page" : undefined}>{t.catalog.currencies}</Link>
              <Link href={unitsHref} aria-current={pathname === unitsHref ? "page" : undefined}>{t.catalog.units}</Link>
            </>
          ) : null}
          {current?.permissions.includes("audit.read") ? (
            <Link href={auditHref} aria-current={pathname === auditHref ? "page" : undefined}>{t.audit.navLabel}</Link>
          ) : null}
          <Link href={accountHref} aria-current={pathname === accountHref ? "page" : undefined}>{t.account.navLabel}</Link>
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          <label>
            <span className="muted" style={{ marginRight: 8 }}>{t.shell.organization}</span>
            <select
              value={orgs.some((o) => o.id === orgId) ? orgId : ""}
              onChange={(e) => router.push(e.target.value === NEW_ORG ? "/onboarding" : `/o/${e.target.value}`)}
              aria-label={t.shell.organization}
            >
              {!orgs.some((o) => o.id === orgId) ? <option value="" disabled>—</option> : null}
              {orgs.map((o) => (
                <option key={o.id} value={o.id}>{o.name}</option>
              ))}
              <option value={NEW_ORG}>{t.shell.newOrg}</option>
            </select>
          </label>
          <div className="topbar-user">
            <LanguageSwitcher />
            <span className="muted">{user.full_name}</span>
            <Button variant="secondary" onClick={async () => { await logout(); router.replace("/login"); }}>
              {t.common.logout}
            </Button>
          </div>
        </header>
        <main className="content">
          {current ? (
            children
          ) : (
            <div className="card">
              <h1>{t.dashboard.notFoundTitle}</h1>
              <p className="muted">{t.dashboard.notFoundBody}</p>
              <Link href="/">{t.dashboard.toRoot}</Link>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
