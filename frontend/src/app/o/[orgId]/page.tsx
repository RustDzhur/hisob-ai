"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ru } from "@/i18n/ru";
import { useSession } from "@/lib/session";

export default function DashboardPage() {
  const { orgId } = useParams<{ orgId: string }>();
  const { state } = useSession();
  if (state.status !== "ready") return null;
  const org = state.orgs.find((o) => o.id === orgId);

  if (!org) {
    return (
      <div className="card">
        <h1>{ru.dashboard.notFoundTitle}</h1>
        <p className="muted">{ru.dashboard.notFoundBody}</p>
        <Link href="/">{ru.dashboard.toRoot}</Link>
      </div>
    );
  }

  return (
    <>
      <h1>{ru.dashboard.title}</h1>
      <p className="muted">{ru.dashboard.welcome}, {state.user.full_name}.</p>
      <section className="card" aria-labelledby="org-card" style={{ marginBottom: 16 }}>
        <h2 id="org-card">{ru.dashboard.orgCard}: {org.name}</h2>
        <dl className="kv">
          <dt>{ru.dashboard.tin}</dt>
          <dd>{org.tin ?? ru.dashboard.tinEmpty}</dd>
          <dt>{ru.dashboard.timezone}</dt>
          <dd>{org.timezone}</dd>
          <dt>{ru.dashboard.currency}</dt>
          <dd>{org.base_currency}</dd>
          <dt>{ru.dashboard.role}</dt>
          <dd>{ru.roles[org.role] ?? org.role}</dd>
        </dl>
      </section>
      <section className="card">
        <h2>{ru.dashboard.emptyTitle}</h2>
        <p className="muted">{ru.dashboard.emptyBody}</p>
      </section>
    </>
  );
}
