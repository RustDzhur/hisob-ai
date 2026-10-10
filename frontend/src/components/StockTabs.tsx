"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useT } from "@/i18n/LocaleProvider";

/** Три раздела склада: остатки, операции, движения. */
export function StockTabs() {
  const t = useT();
  const pathname = usePathname();
  const { orgId } = useParams<{ orgId: string }>();
  const base = `/o/${orgId}/stock`;
  const tabs = [
    { href: base, label: t.stock.balances, active: pathname === base },
    { href: `${base}/operations`, label: t.stock.operations, active: pathname.startsWith(`${base}/operations`) },
    { href: `${base}/counts`, label: t.stock.counts, active: pathname.startsWith(`${base}/counts`) },
    { href: `${base}/reservations`, label: t.stock.reservations, active: pathname === `${base}/reservations` },
    { href: `${base}/movements`, label: t.stock.movements, active: pathname === `${base}/movements` },
  ];
  return (
    <nav className="tabs" aria-label={t.stock.navLabel}>
      {tabs.map((x) => (
        <Link key={x.href} href={x.href} aria-current={x.active ? "page" : undefined}>{x.label}</Link>
      ))}
    </nav>
  );
}
