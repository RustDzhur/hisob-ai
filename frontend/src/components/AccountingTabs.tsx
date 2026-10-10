"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useT } from "@/i18n/LocaleProvider";

export function AccountingTabs() {
  const t = useT();
  const pathname = usePathname();
  const { orgId } = useParams<{ orgId: string }>();
  const base = `/o/${orgId}/accounting`;
  const tabs = [
    { href: base, label: t.accounting.entries, active: pathname === base || pathname.startsWith(`${base}/entries`) },
    { href: `${base}/accounts`, label: t.accounting.accounts, active: pathname.startsWith(`${base}/accounts`) },
    { href: `${base}/rules`, label: t.accounting.rules, active: pathname.startsWith(`${base}/rules`) },
    { href: `${base}/rates`, label: t.accounting.rates, active: pathname === `${base}/rates` },
    { href: `${base}/taxes`, label: t.accounting.taxes, active: pathname === `${base}/taxes` },
    { href: `${base}/periods`, label: t.accounting.periods, active: pathname === `${base}/periods` },
    { href: `${base}/trial-balance`, label: t.accounting.trial, active: pathname === `${base}/trial-balance` },
  ];
  return (
    <nav className="tabs" aria-label={t.accounting.navLabel}>
      {tabs.map((x) => <Link key={x.href} href={x.href} aria-current={x.active ? "page" : undefined}>{x.label}</Link>)}
    </nav>
  );
}
