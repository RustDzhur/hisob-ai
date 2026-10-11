"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { BankTabs } from "@/components/BankTabs";
import { DataTable, type Column } from "@/components/DataTable";
import { Button, Skeleton } from "@/components/ui";
import { useFormat, useLocale, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type BankAccount, type BankTransaction, type Page } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import { useSession } from "@/lib/session";
import { useListParams } from "@/lib/useListParams";

export function BankTransactionsView() {
  const t = useT();
  const { locale } = useLocale();
  const { dateOnly } = useFormat();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("bank.read");

  const { state, update } = useListParams({ sort: "txn_date", order: "desc", filters: { account_id: "", status: "all", direction: "all", import_id: "", date_from: "", date_to: "" } });
  const [searchText, setSearchText] = useState(state.q);
  const [data, setData] = useState<Page<BankTransaction> | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [accounts, setAccounts] = useState<BankAccount[]>([]);

  useEffect(() => {
    if (searchText === state.q) return;
    const id = setTimeout(() => update({ q: searchText }), 300);
    return () => clearTimeout(id);
  }, [searchText, state.q, update]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const qs = new URLSearchParams({ page: String(state.page), page_size: "25", sort: state.sort, order: state.order, status: state.filters.status, direction: state.filters.direction });
    if (state.filters.account_id) qs.set("account_id", state.filters.account_id);
    if (state.filters.import_id) qs.set("import_id", state.filters.import_id);
    if (state.filters.date_from) qs.set("date_from", state.filters.date_from);
    if (state.filters.date_to) qs.set("date_to", state.filters.date_to);
    if (state.q) qs.set("q", state.q);
    try {
      setData(await api<Page<BankTransaction>>(`/organizations/${orgId}/bank/transactions?${qs}`));
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setLoading(false);
    }
  }, [orgId, state]);
  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);
  useEffect(() => {
    if (canRead) api<Page<BankAccount>>(`/organizations/${orgId}/bank/accounts?page_size=100&status=all`).then((p) => setAccounts(p.items)).catch(() => setAccounts([]));
  }, [canRead, orgId]);

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) return <div className="card"><h1>{t.catalog.noAccessTitle}</h1><p className="muted">{t.bank.noAccessBody}</p></div>;

  const columns: Column<BankTransaction>[] = [
    { key: "date", header: t.bank.colDate, sortKey: "txn_date", render: (r) => dateOnly(r.txn_date) },
    { key: "acc", header: t.bank.colAccount, render: (r) => r.bank_account.name },
    {
      key: "amount", header: t.bank.colAmount, sortKey: "amount",
      render: (r) => <span className={`num ${r.amount.startsWith("-") ? "qty-neg" : "qty-pos"}`}>{formatMoney(r.amount, r.currency, locale)}</span>,
    },
    { key: "desc", header: t.bank.colDescription, render: (r) => <>{r.description ?? "—"}{r.reference ? <div className="sub">{r.reference}</div> : null}</> },
    { key: "cp", header: t.bank.colCounterparty, render: (r) => <>{r.counterparty ? <Link href={`/o/${orgId}/counterparties`}>{r.counterparty.name}</Link> : (r.counterparty_name ?? "—")}{r.counterparty_tin ? <div className="sub">{r.counterparty_tin}</div> : null}</> },
    { key: "status", header: t.bank.colStatus, render: (r) => (
      <>
        <span className={`badge ${r.status === "MATCHED" ? "badge-ok" : r.status === "IGNORED" ? "badge-muted" : "badge-warn"}`}>{t.bank.statuses[r.status]}</span>
        {r.status === "UNMATCHED" && Number(r.matched_amount ?? 0) > 0 ? <div className="sub">{t.bank.partial}: {formatMoney(r.matched_amount ?? "0", r.currency, locale)}</div> : null}
      </>
    ) },
    { key: "act", header: "", render: (r) => <Link href={`/o/${orgId}/bank/transactions/${r.id}`}>{t.bank.reconcile}</Link> },
  ];
  const f = state.filters;
  const filtered = !!(state.q || f.account_id || f.status !== "all" || f.direction !== "all" || f.import_id || f.date_from || f.date_to);

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{t.bank.txnTitle}</h1>
        <Link className="btn" href={`/o/${orgId}/bank/accounts`}>{t.bank.accountsTab}</Link>
      </div>
      <BankTabs />
      <p className="hint">{t.bank.note}</p>
      <div className="toolbar">
        <input type="search" value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder={t.bank.searchPlaceholder} aria-label={t.list.search} maxLength={100} />
        <select aria-label={t.bank.colAccount} value={f.account_id} onChange={(e) => update({ filters: { account_id: e.target.value } })}>
          <option value="">{t.bank.allAccounts}</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <select aria-label={t.bank.colStatus} value={f.status} onChange={(e) => update({ filters: { status: e.target.value } })}>
          {Object.entries(t.bank.statusFilters).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select aria-label={t.bank.colAmount} value={f.direction} onChange={(e) => update({ filters: { direction: e.target.value } })}>
          {Object.entries(t.bank.directions).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <input type="date" aria-label={t.bank.periodFrom} value={f.date_from} onChange={(e) => update({ filters: { date_from: e.target.value } })} />
        <input type="date" aria-label={t.bank.periodTo} value={f.date_to} onChange={(e) => update({ filters: { date_to: e.target.value } })} />
        {filtered ? <Button variant="secondary" onClick={() => { setSearchText(""); update({ q: "", filters: { account_id: "", status: "all", direction: "all", import_id: "", date_from: "", date_to: "" } }); }}>{t.list.reset}</Button> : null}
      </div>
      <DataTable
        caption={t.bank.txnTitle} columns={columns} rows={data?.items ?? null} total={data?.total ?? 0}
        page={state.page} pageSize={data?.page_size ?? 25} loading={loading} error={error} onRetry={load}
        sort={state.sort} order={state.order}
        onSort={(key, order) => update({ sort: key, order: order ?? (state.sort === key && state.order === "desc" ? "asc" : "desc") })}
        onPage={(p) => update({ page: p })} rowKey={(r) => r.id}
        empty={filtered ? <p className="muted">{t.list.emptyFiltered}</p> : <p className="muted">{t.bank.emptyTxn}</p>}
      />
    </div>
  );
}
