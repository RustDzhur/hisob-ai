"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { StockTabs } from "@/components/StockTabs";
import { Button, Skeleton } from "@/components/ui";
import { useFormat, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type Page, type StockOperationItem } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useListParams } from "@/lib/useListParams";

export function OperationsView() {
  const t = useT();
  const { dateOnly } = useFormat();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("stock.read");
  const canManage = !!org?.permissions.includes("stock.manage");

  const { state, update } = useListParams({ sort: "op_date", order: "desc", filters: { status: "all", kind: "" } });
  const [searchText, setSearchText] = useState(state.q);
  const [data, setData] = useState<Page<StockOperationItem> | null>(null);
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
    const qs = new URLSearchParams({ page: String(state.page), page_size: "25", sort: state.sort, order: state.order, status: state.filters.status });
    if (state.filters.kind) qs.set("kind", state.filters.kind);
    if (state.q) qs.set("q", state.q);
    try {
      setData(await api<Page<StockOperationItem>>(`/organizations/${orgId}/stock/operations?${qs}`));
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
      <div className="card">
        <h1>{t.catalog.noAccessTitle}</h1>
        <p className="muted">{t.stock.noAccessBody}</p>
      </div>
    );
  }

  const columns: Column<StockOperationItem>[] = [
    { key: "date", header: t.stock.colDate, sortKey: "op_date", render: (o) => <Link href={`/o/${orgId}/stock/operations/${o.id}`}>{dateOnly(o.op_date)}</Link> },
    { key: "kind", header: t.stock.colKind, render: (o) => (
      <>
        {t.stock.kinds[o.kind] ?? o.kind}
        {o.is_reversal ? <> <span className="badge badge-muted">{t.stock.reversalBadge}</span></> : null}
        {o.reversed ? <> <span className="badge badge-muted">{t.stock.reversedBadge}</span></> : null}
      </>
    ) },
    { key: "wh", header: t.stock.colWarehouse, render: (o) => (o.to_warehouse_name ? `${o.warehouse_name} → ${o.to_warehouse_name}` : o.warehouse_name) },
    { key: "ref", header: t.stock.colReference, render: (o) => o.reference ?? "—" },
    { key: "lines", header: t.stock.colLines, render: (o) => o.lines_count },
    { key: "status", header: t.stock.colStatus, render: (o) => <span className={`badge ${o.status === "POSTED" ? "badge-ok" : "badge-warn"}`}>{t.stock.statuses[o.status] ?? o.status}</span> },
  ];
  const filtered = !!(state.q || state.filters.status !== "all" || state.filters.kind);

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{t.stock.operationsTitle}</h1>
        {canManage ? <Link className="btn" href={`/o/${orgId}/stock/operations/new`}>{t.stock.newOperation}</Link> : null}
      </div>
      <StockTabs />
      <div className="toolbar">
        <input type="search" value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder={t.stock.colReference} aria-label={t.list.search} maxLength={100} />
        <select aria-label={t.stock.colStatus} value={state.filters.status} onChange={(e) => update({ filters: { status: e.target.value } })}>
          {Object.entries(t.stock.statusFilters).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select aria-label={t.stock.colKind} value={state.filters.kind} onChange={(e) => update({ filters: { kind: e.target.value } })}>
          <option value="">{t.stock.allKinds}</option>
          {Object.entries(t.stock.kinds).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        {filtered ? <Button variant="secondary" onClick={() => { setSearchText(""); update({ q: "", filters: { status: "all", kind: "" } }); }}>{t.list.reset}</Button> : null}
      </div>
      <DataTable
        caption={t.stock.operationsTitle} columns={columns} rows={data?.items ?? null} total={data?.total ?? 0}
        page={state.page} pageSize={data?.page_size ?? 25} loading={loading} error={error} onRetry={load}
        sort={state.sort} order={state.order}
        onSort={(key, order) => update({ sort: key, order: order ?? (state.sort === key && state.order === "desc" ? "asc" : "desc") })}
        onPage={(p) => update({ page: p })} rowKey={(o) => o.id}
        empty={filtered ? <p className="muted">{t.list.emptyFiltered}</p> : (
          <>
            <p className="muted">{t.stock.emptyOperations}</p>
            {canManage ? <Link className="btn" href={`/o/${orgId}/stock/operations/new`}>{t.stock.newOperation}</Link> : null}
          </>
        )}
      />
    </div>
  );
}
