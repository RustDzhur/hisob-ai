"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useT } from "@/i18n/LocaleProvider";

export function BankTabs() {
  const t = useT();
  const pathname = usePathname();
  const { orgId } = useParams<{ orgId: string }>();
  const base = `/o/${orgId}/bank`;
  const tabs = [
    { href: base, label: t.bank.txnTab, active: pathname === base },
    { href: `${base}/accounts`, label: t.bank.accountsTab, active: pathname.startsWith(`${base}/accounts`) },
    { href: `${base}/reports`, label: t.bank.reportsTab, active: pathname.startsWith(`${base}/reports`) },
  ];
  return (
    <nav className="tabs" aria-label={t.bank.navLabel}>
      {tabs.map((x) => <Link key={x.href} href={x.href} aria-current={x.active ? "page" : undefined}>{x.label}</Link>)}
    </nav>
  );
}
