"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AccountingTabs } from "@/components/AccountingTabs";
import { DataTable, type Column } from "@/components/DataTable";
import { Button, Skeleton } from "@/components/ui";
import { useFormat, useLocale, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type JournalEntryItem, type Page } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import { useSession } from "@/lib/session";
import { useListParams } from "@/lib/useListParams";

export function EntriesView() {
  const t = useT();
  const { locale } = useLocale();
  const { dateOnly } = useFormat();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("accounting.read");
  const canPost = !!org?.permissions.includes("accounting.post");

  const { state, update } = useListParams({ sort: "entry_date", order: "desc", filters: { date_from: "", date_to: "" } });
  const [searchText, setSearchText] = useState(state.q);
  const [data, setData] = useState<Page<JournalEntryItem> | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (searchText === state.q) return;
    const id = setTimeout(() => update({ q: searchText }), 300);
    return () => clearTimeout(id);
  }, [searchText, state.q, update]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const qs = new URLSearchParams({ page: String(state.page), page_size: "25", sort: state.sort, order: state.order });
    if (state.filters.date_from) qs.set("date_from", state.filters.date_from);
    if (state.filters.date_to) qs.set("date_to", state.filters.date_to);
    if (state.q) qs.set("q", state.q);
    try {
      setData(await api<Page<JournalEntryItem>>(`/organizations/${orgId}/accounting/entries?${qs}`));
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setLoading(false);
    }
  }, [orgId, state]);

  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) {
    return (
      <div className="card"><h1>{t.catalog.noAccessTitle}</h1><p className="muted">{t.accounting.noAccessBody}</p></div>
    );
  }

  const columns: Column<JournalEntryItem>[] = [
    { key: "num", header: t.accounting.colNumber, sortKey: "number", render: (e) => <Link href={`/o/${orgId}/accounting/entries/${e.id}`}>{e.number}</Link> },
    { key: "date", header: t.accounting.colDate, sortKey: "entry_date", render: (e) => dateOnly(e.entry_date) },
    {
      key: "desc", header: t.accounting.colDescription,
      render: (e) => (
        <>
          {e.description}
          {e.is_reversal ? <> <span className="badge badge-muted">{t.accounting.reversalBadge}</span></> : null}
          {e.reversed ? <> <span className="badge badge-muted">{t.accounting.reversedBadge}</span></> : null}
        </>
      ),
    },
    { key: "total", header: t.accounting.colTotal, render: (e) => <span className="num">{formatMoney(e.total, e.currency, locale)}</span> },
  ];
  const filtered = !!(state.q || state.filters.date_from || state.filters.date_to);

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{t.accounting.entriesTitle}</h1>
        {canPost ? <Link className="btn" href={`/o/${orgId}/accounting/entries/new`}>{t.accounting.newEntry}</Link> : null}
      </div>
      <AccountingTabs />
      <p className="hint">{t.accounting.note}</p>
      <div className="toolbar">
        <input type="search" value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder={t.accounting.searchPlaceholder} aria-label={t.list.search} maxLength={100} />
        <label><span className="muted">{t.accounting.dateFrom} </span><input type="date" value={state.filters.date_from} onChange={(e) => update({ filters: { date_from: e.target.value } })} /></label>
        <label><span className="muted">{t.accounting.dateTo} </span><input type="date" value={state.filters.date_to} onChange={(e) => update({ filters: { date_to: e.target.value } })} /></label>
        {filtered ? <Button variant="secondary" onClick={() => { setSearchText(""); update({ q: "", filters: { date_from: "", date_to: "" } }); }}>{t.list.reset}</Button> : null}
      </div>
      <DataTable
        caption={t.accounting.entriesTitle} columns={columns} rows={data?.items ?? null} total={data?.total ?? 0}
        page={state.page} pageSize={data?.page_size ?? 25} loading={loading} error={error} onRetry={load}
        sort={state.sort} order={state.order}
        onSort={(key, order) => update({ sort: key, order: order ?? (state.sort === key && state.order === "desc" ? "asc" : "desc") })}
        onPage={(p) => update({ page: p })} rowKey={(e) => e.id}
        empty={filtered ? <p className="muted">{t.list.emptyFiltered}</p> : (
          <>
            <p className="muted">{t.accounting.emptyEntries}</p>
            {canPost ? <Link className="btn" href={`/o/${orgId}/accounting/entries/new`}>{t.accounting.newEntry}</Link> : null}
          </>
        )}
      />
    </div>
  );
}
