"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useT } from "@/i18n/LocaleProvider";
import { useSession } from "@/lib/session";

export default function DashboardPage() {
  const t = useT();
  const { orgId } = useParams<{ orgId: string }>();
  const { state } = useSession();
  if (state.status !== "ready") return null;
  const org = state.orgs.find((o) => o.id === orgId);

  if (!org) {
    return (
      <div className="card">
        <h1>{t.dashboard.notFoundTitle}</h1>
        <p className="muted">{t.dashboard.notFoundBody}</p>
        <Link href="/">{t.dashboard.toRoot}</Link>
      </div>
    );
  }

  return (
    <>
      <h1>{t.dashboard.title}</h1>
      <p className="muted">{t.dashboard.welcome}, {state.user.full_name}.</p>
      <section className="card" aria-labelledby="org-card" style={{ marginBottom: 16 }}>
        <h2 id="org-card">{t.dashboard.orgCard}: {org.name}</h2>
        <dl className="kv">
          <dt>{t.dashboard.tin}</dt>
          <dd>{org.tin ?? t.dashboard.tinEmpty}</dd>
          <dt>{t.dashboard.timezone}</dt>
          <dd>{org.timezone}</dd>
          <dt>{t.dashboard.currency}</dt>
          <dd>{org.base_currency}</dd>
          <dt>{t.dashboard.role}</dt>
          <dd>{t.roles[org.role] ?? org.role}</dd>
        </dl>
      </section>
      <section className="card">
        <h2>{t.dashboard.emptyTitle}</h2>
        <p className="muted">{t.dashboard.emptyBody}</p>
      </section>
    </>
  );
}
