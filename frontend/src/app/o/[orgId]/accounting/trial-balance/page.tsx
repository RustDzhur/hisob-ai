"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AccountingTabs } from "@/components/AccountingTabs";
import { Banner, Button, ErrorState, Skeleton } from "@/components/ui";
import { useLocale, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type Currency, type TrialBalance } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import { useSession } from "@/lib/session";
import Link from "next/link";

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export default function TrialBalancePage() {
  const t = useT();
  const { locale } = useLocale();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("accounting.read");
  const now = new Date();
  const [from, setFrom] = useState(iso(new Date(now.getFullYear(), now.getMonth(), 1)));
  const [to, setTo] = useState(iso(new Date(now.getFullYear(), now.getMonth() + 1, 0)));
  const [currency, setCurrency] = useState("");
  const [currencies, setCurrencies] = useState<Currency[]>([]);
  const [data, setData] = useState<TrialBalance | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!canRead) return;
    api<Currency[]>(`/organizations/${orgId}/currencies`).then((list) => {
      const enabled = list.filter((c) => c.enabled);
      setCurrencies(enabled);
      setCurrency((cur) => cur || enabled.find((c) => c.is_base)?.code || "");
    }).catch(() => setCurrencies([]));
  }, [canRead, orgId]);

  async function run(cur = currency) {
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({ date_from: from, date_to: to });
      if (cur) qs.set("currency", cur);
      setData(await api<TrialBalance>(`/organizations/${orgId}/accounting/reports/trial-balance?${qs}`));
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    if (canRead && currency) void run(currency);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canRead, currency]);

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) return <div className="card"><h1>{t.catalog.noAccessTitle}</h1><p className="muted">{t.accounting.noAccessBody}</p></div>;

  const m = (v: string) => (Number(v) === 0 ? "" : formatMoney(v, data?.currency ?? "", locale));
  return (
    <div className="stack">
      <div className="page-head"><h1>{t.accounting.trialTitle}</h1></div>
      <AccountingTabs />
      <div className="toolbar">
        <label><span className="muted">{t.accounting.dateFrom} </span><input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label><span className="muted">{t.accounting.dateTo} </span><input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        <select aria-label={t.accounting.fCurrency} value={currency} onChange={(e) => setCurrency(e.target.value)}>
          {currencies.map((c) => <option key={c.code} value={c.code}>{c.code}</option>)}
        </select>
        <Button variant="secondary" loading={loading} onClick={() => void run()}>{t.accounting.show}</Button>
      </div>
      {error ? <ErrorState error={error} onRetry={() => void run()} /> : null}
      {data ? (
        <>
          <Banner kind={data.balanced ? "info" : "error"}>{data.balanced ? t.accounting.trialBalanced : t.accounting.trialUnbalanced}</Banner>
          {data.rows.length === 0 ? <div className="card"><p className="muted">{t.accounting.emptyTrial}</p></div> : (
            <section className="card">
              <div className="table-wrap">
                <table className="lines-table">
                  <thead>
                    <tr><th rowSpan={2}>{t.accounting.colCode}</th><th rowSpan={2}>{t.accounting.colName}</th><th colSpan={2}>{t.accounting.colOpening}</th><th colSpan={2}>{t.accounting.colTurnover}</th><th colSpan={2}>{t.accounting.colClosing}</th></tr>
                    <tr><th>{t.accounting.colDebit}</th><th>{t.accounting.colCredit}</th><th>{t.accounting.colDebit}</th><th>{t.accounting.colCredit}</th><th>{t.accounting.colDebit}</th><th>{t.accounting.colCredit}</th></tr>
                  </thead>
                  <tbody>
                    {data.rows.map((r) => (
                      <tr key={r.account_id}>
                        <td><Link href={`/o/${orgId}/accounting/accounts/${r.account_id}`}>{r.code}</Link></td>
                        <td>{r.name}</td>
                        <td className="num">{m(r.opening_debit)}</td><td className="num">{m(r.opening_credit)}</td>
                        <td className="num">{m(r.turnover_debit)}</td><td className="num">{m(r.turnover_credit)}</td>
                        <td className="num">{m(r.closing_debit)}</td><td className="num">{m(r.closing_credit)}</td>
                      </tr>
                    ))}
                  </tbody>
                  {data.totals ? (
                    <tfoot>
                      <tr>
                        <td /><td><strong>{t.accounting.totals}</strong></td>
                        <td className="num">{m(data.totals.opening_debit)}</td><td className="num">{m(data.totals.opening_credit)}</td>
                        <td className="num">{m(data.totals.turnover_debit)}</td><td className="num">{m(data.totals.turnover_credit)}</td>
                        <td className="num">{m(data.totals.closing_debit)}</td><td className="num">{m(data.totals.closing_credit)}</td>
                      </tr>
                    </tfoot>
                  ) : null}
                </table>
              </div>
            </section>
          )}
        </>
      ) : null}
    </div>
  );
}
