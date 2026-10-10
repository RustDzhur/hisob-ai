"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AccountingTabs } from "@/components/AccountingTabs";
import { Button, ErrorState, Skeleton } from "@/components/ui";
import { useFormat, useLocale, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type Currency, type Ledger } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import { useSession } from "@/lib/session";

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export default function LedgerPage() {
  const t = useT();
  const { locale } = useLocale();
  const { dateOnly } = useFormat();
  const { orgId, accountId } = useParams<{ orgId: string; accountId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("accounting.read");
  const now = new Date();
  const [from, setFrom] = useState(iso(new Date(now.getFullYear(), 0, 1)));
  const [to, setTo] = useState(iso(new Date(now.getFullYear(), 11, 31)));
  const [currency, setCurrency] = useState("");
  const [currencies, setCurrencies] = useState<Currency[]>([]);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Ledger | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    if (!canRead) return;
    api<Currency[]>(`/organizations/${orgId}/currencies`).then((list) => {
      const enabled = list.filter((c) => c.enabled);
      setCurrencies(enabled);
      setCurrency((cur) => cur || enabled.find((c) => c.is_base)?.code || "");
    }).catch(() => setCurrencies([]));
  }, [canRead, orgId]);

  const load = useCallback(async () => {
    if (!currency) return;
    setError(null);
    try {
      const qs = new URLSearchParams({ date_from: from, date_to: to, currency, page: String(page), page_size: "50" });
      setData(await api<Ledger>(`/organizations/${orgId}/accounting/accounts/${accountId}/ledger?${qs}`));
    } catch (e) {
      setError(toApiError(e));
    }
  }, [orgId, accountId, from, to, currency, page]);
  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) return <div className="card"><h1>{t.catalog.noAccessTitle}</h1><p className="muted">{t.accounting.noAccessBody}</p></div>;
  if (error) return error.status === 404 ? <div className="card"><h1>{t.accounting.notFound}</h1><Link href={`/o/${orgId}/accounting/accounts`}>{t.accounting.accounts}</Link></div> : <ErrorState error={error} onRetry={load} />;

  const m = (v: string) => (Number(v) === 0 ? "" : formatMoney(v, currency, locale));
  const pages = data ? Math.max(1, Math.ceil(data.total / 50)) : 1;
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <p style={{ margin: 0 }}><Link href={`/o/${orgId}/accounting/accounts`}>← {t.accounting.accounts}</Link></p>
          <h1 style={{ marginTop: 4 }}>{t.accounting.ledgerTitle}{data ? `: ${data.code} · ${data.name}` : ""}</h1>
        </div>
      </div>
      <AccountingTabs />
      <div className="toolbar">
        <label><span className="muted">{t.accounting.dateFrom} </span><input type="date" value={from} onChange={(e) => { setFrom(e.target.value); setPage(1); }} /></label>
        <label><span className="muted">{t.accounting.dateTo} </span><input type="date" value={to} onChange={(e) => { setTo(e.target.value); setPage(1); }} /></label>
        <select aria-label={t.accounting.fCurrency} value={currency} onChange={(e) => { setCurrency(e.target.value); setPage(1); }}>
          {currencies.map((c) => <option key={c.code} value={c.code}>{c.code}</option>)}
        </select>
      </div>
      {!data ? <Skeleton lines={5} /> : (
        <section className="card">
          <p><strong>{t.accounting.colOpening}:</strong> {Number(data.opening_debit) !== 0 ? `${t.accounting.colDebit} ${formatMoney(data.opening_debit, currency, locale)}` : Number(data.opening_credit) !== 0 ? `${t.accounting.colCredit} ${formatMoney(data.opening_credit, currency, locale)}` : "0"}</p>
          {data.rows.length === 0 ? <p className="muted">{t.accounting.emptyTrial}</p> : (
            <div className="table-wrap">
              <table className="lines-table">
                <thead><tr><th>{t.accounting.colNumber}</th><th>{t.accounting.colDate}</th><th>{t.accounting.colDescription}</th><th>{t.accounting.colDebit}</th><th>{t.accounting.colCredit}</th><th>{t.accounting.colBalance} ({t.accounting.colDebit})</th><th>{t.accounting.colBalance} ({t.accounting.colCredit})</th></tr></thead>
                <tbody>
                  {data.rows.map((r, i) => (
                    <tr key={`${r.entry_id}-${i}`}>
                      <td><Link href={`/o/${orgId}/accounting/entries/${r.entry_id}`}>{r.number}</Link></td>
                      <td>{dateOnly(r.entry_date)}</td>
                      <td>{r.description}{r.memo ? <span className="hint"> · {r.memo}</span> : null}</td>
                      <td className="num">{m(r.debit)}</td><td className="num">{m(r.credit)}</td>
                      <td className="num">{m(r.balance_debit)}</td><td className="num">{m(r.balance_credit)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {pages > 1 ? (
            <div className="toolbar">
              <Button variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹</Button>
              <span>{page} / {pages}</span>
              <Button variant="secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>›</Button>
            </div>
          ) : null}
        </section>
      )}
    </div>
  );
}
