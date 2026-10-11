"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { BankTabs } from "@/components/BankTabs";
import { Banner, Button, Skeleton } from "@/components/ui";
import { errorText, fill } from "@/i18n/errors";
import { useFormat, useLocale, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type BankAccount, type BankMatchReport, type BankSummary, type Page } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import { useSession } from "@/lib/session";

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export default function BankReportsPage() {
  const t = useT();
  const { locale } = useLocale();
  const { dateOnly } = useFormat();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("bank.read");
  const now = new Date();
  const [from, setFrom] = useState(iso(new Date(now.getFullYear(), now.getMonth(), 1)));
  const [to, setTo] = useState(iso(new Date(now.getFullYear(), now.getMonth() + 1, 0)));
  const [accountId, setAccountId] = useState("");
  const [accounts, setAccounts] = useState<BankAccount[]>([]);
  const [summary, setSummary] = useState<BankSummary | null>(null);
  const [matches, setMatches] = useState<BankMatchReport | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);

  const qs = useCallback(() => {
    const p = new URLSearchParams({ date_from: from, date_to: to });
    if (accountId) p.set("account_id", accountId);
    return p;
  }, [from, to, accountId]);

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const p = qs();
      const [s, m] = await Promise.all([
        api<BankSummary>(`/organizations/${orgId}/bank/reports/summary?${p}`),
        api<BankMatchReport>(`/organizations/${orgId}/bank/reports/matches?${p}`),
      ]);
      setSummary(s);
      setMatches(m);
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setLoading(false);
    }
  }, [orgId, qs]);

  useEffect(() => {
    if (!canRead) return;
    api<Page<BankAccount>>(`/organizations/${orgId}/bank/accounts?page_size=100&status=all`).then((p) => setAccounts(p.items)).catch(() => setAccounts([]));
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canRead, orgId]);

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) return <div className="card"><h1>{t.catalog.noAccessTitle}</h1><p className="muted">{t.bank.noAccessBody}</p></div>;

  const csvBase = `/api/v1/organizations/${orgId}/bank/reports`;
  // drill-down: список операций с теми же счётом и периодом
  const drill = (accId: string, status?: string) => {
    const p = new URLSearchParams({ account_id: accId, date_from: from, date_to: to });
    if (status) p.set("status", status);
    return `/o/${orgId}/bank?${p}`;
  };

  return (
    <div className="stack">
      <div className="page-head"><h1>{t.bank.reportsTitle}</h1></div>
      <BankTabs />
      <p className="hint">{t.bank.reportsNote}</p>
      <div className="toolbar">
        <label>{t.bank.periodFrom} <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>{t.bank.periodTo} <input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        <select aria-label={t.bank.colAccount} value={accountId} onChange={(e) => setAccountId(e.target.value)}>
          <option value="">{t.bank.allAccountsOpt}</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <Button loading={loading} onClick={() => void run()}>{t.bank.show}</Button>
      </div>
      {error ? <Banner>{errorText(t, error)}</Banner> : null}
      <div className="toolbar">
        <a className="btn btn-secondary" href={`${csvBase}/summary.csv?${qs()}`}>{t.bank.exportSummary}</a>
        <a className="btn btn-secondary" href={`${csvBase}/matches.csv?${qs()}`}>{t.bank.exportMatches}</a>
        <a className="btn btn-secondary" href={`${csvBase}/transactions.csv?${qs()}`}>{t.bank.exportTxns}</a>
      </div>

      <section className="card" aria-labelledby="bs-h">
        <h2 id="bs-h">{t.bank.reportsTitle}</h2>
        {summary && summary.rows.length === 0 ? <p className="muted">{t.bank.noReportRows}</p> : null}
        {summary && summary.rows.length > 0 ? (
          <div className="table-wrap">
            <table className="lines-table" data-testid="bank-summary">
              <thead>
                <tr>
                  <th>{t.bank.colAccount}</th><th>{t.bank.colCurrency}</th><th className="num">{t.bank.colOpening}</th><th className="num">{t.bank.colIncoming}</th>
                  <th className="num">{t.bank.colOutgoing}</th><th className="num">{t.bank.colNet}</th><th className="num">{t.bank.colClosing}</th>
                  <th className="num">{t.bank.colCount}</th><th className="num">{t.bank.colUnmatched}</th><th className="num">{t.bank.colMatched}</th>
                  <th className="num">{t.bank.colIgnored}</th><th className="num">{t.bank.colUnmatchedAmount}</th>
                </tr>
              </thead>
              <tbody>
                {summary.rows.map((r) => (
                  <tr key={`${r.bank_account.id}-${r.currency}`}>
                    <td><Link href={drill(r.bank_account.id)} title={t.bank.drillTitle}>{r.bank_account.name}</Link></td>
                    <td>{r.currency}</td>
                    <td className="num">{formatMoney(r.opening, r.currency, locale)}</td>
                    <td className="num">{formatMoney(r.incoming, r.currency, locale)}</td>
                    <td className="num">{formatMoney(r.outgoing, r.currency, locale)}</td>
                    <td className="num">{formatMoney(r.net, r.currency, locale)}</td>
                    <td className="num">{formatMoney(r.closing, r.currency, locale)}</td>
                    <td className="num"><Link href={drill(r.bank_account.id)}>{r.txn_count}</Link></td>
                    <td className="num"><Link href={drill(r.bank_account.id, "UNMATCHED")}>{r.unmatched_count}</Link></td>
                    <td className="num"><Link href={drill(r.bank_account.id, "MATCHED")}>{r.matched_count}</Link></td>
                    <td className="num"><Link href={drill(r.bank_account.id, "IGNORED")}>{r.ignored_count}</Link></td>
                    <td className="num">{formatMoney(r.unmatched_amount, r.currency, locale)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {summary ? <p className="hint">{summary.note}</p> : null}
      </section>

      <section className="card" aria-labelledby="bm-h">
        <h2 id="bm-h">{t.bank.matchesReportTitle}</h2>
        {matches && matches.rows.length === 0 ? <p className="muted">{t.bank.noMatchesReport}</p> : null}
        {matches && matches.rows.length > 0 ? (
          <div className="table-wrap">
            <table className="lines-table" data-testid="bank-matches-report">
              <thead><tr><th>{t.bank.colDate}</th><th>{t.bank.colAccount}</th><th className="num">{t.bank.colAmount}</th><th>{t.bank.colDocument}</th><th>{t.bank.colCounterparty}</th><th>{t.bank.colWho}</th></tr></thead>
              <tbody>
                {matches.rows.map((r) => (
                  <tr key={r.match_id}>
                    <td><Link href={`/o/${orgId}/bank/transactions/${r.transaction_id}`}>{dateOnly(r.txn_date)}</Link></td>
                    <td>{r.bank_account_name}</td>
                    <td className="num">{formatMoney(r.amount, r.currency, locale)}</td>
                    <td><Link href={`/o/${orgId}/documents/${r.document_id}`}>{r.document_number ?? "—"}</Link></td>
                    <td>{r.counterparty_name ?? "—"}</td>
                    <td>{r.confirmed_by_name ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {matches?.truncated ? <p className="hint">{fill(t.bank.truncatedNote, { shown: matches.rows.length, total: matches.total })}</p> : null}
      </section>
    </div>
  );
}
