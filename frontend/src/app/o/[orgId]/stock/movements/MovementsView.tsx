"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { StockTabs } from "@/components/StockTabs";
import { Button, Skeleton } from "@/components/ui";
import { useFormat, useLocale, useT } from "@/i18n/LocaleProvider";
import { pickName } from "@/i18n/names";
import { api, toApiError, type ApiError, type Page, type StockMovement, type Warehouse } from "@/lib/api";
import { formatQuantity } from "@/lib/money";
import { useSession } from "@/lib/session";
import { useListParams } from "@/lib/useListParams";

export function MovementsView() {
  const t = useT();
  const { locale } = useLocale();
  const { dateOnly } = useFormat();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("stock.read");

  const { state, update } = useListParams({ sort: "created_at", order: "desc", filters: { warehouse_id: "", product_id: "" } });
  const [searchText, setSearchText] = useState(state.q);
  const [data, setData] = useState<Page<StockMovement> | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);

  useEffect(() => {
    if (searchText === state.q) return;
    const id = setTimeout(() => update({ q: searchText }), 300);
    return () => clearTimeout(id);
  }, [searchText, state.q, update]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const qs = new URLSearchParams({ page: String(state.page), page_size: "25" });
    if (state.filters.warehouse_id) qs.set("warehouse_id", state.filters.warehouse_id);
    if (state.filters.product_id) qs.set("product_id", state.filters.product_id);
    if (state.q) qs.set("q", state.q);
    try {
      setData(await api<Page<StockMovement>>(`/organizations/${orgId}/stock/movements?${qs}`));
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
    if (canRead) api<Page<Warehouse>>(`/organizations/${orgId}/warehouses?page_size=100&status=all`).then((p) => setWarehouses(p.items)).catch(() => setWarehouses([]));
  }, [canRead, orgId]);

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) {
    return (
      <div className="card">
        <h1>{t.catalog.noAccessTitle}</h1>
        <p className="muted">{t.stock.noAccessBody}</p>
      </div>
    );
  }

  const columns: Column<StockMovement>[] = [
    { key: "date", header: t.stock.colDate, render: (m) => dateOnly(m.op_date) },
    { key: "kind", header: t.stock.colKind, render: (m) => <Link href={`/o/${orgId}/stock/operations/${m.operation_id}`}>{t.stock.kinds[m.kind] ?? m.kind}{m.reference ? ` · ${m.reference}` : ""}</Link> },
    { key: "wh", header: t.stock.colWarehouse, render: (m) => m.warehouse.name },
    { key: "sku", header: t.stock.colSku, render: (m) => m.sku },
    { key: "name", header: t.stock.colProduct, render: (m) => m.name },
    {
      key: "delta", header: t.stock.colChange,
      render: (m) => {
        const neg = m.quantity_delta.startsWith("-");
        return <span className={`num ${neg ? "qty-neg" : "qty-pos"}`}>{neg ? "" : "+"}{formatQuantity(m.quantity_delta, locale)}</span>;
      },
    },
    { key: "unit", header: t.stock.colUnit, render: (m) => pickName(m.unit, locale) || m.unit.code },
  ];
  const filtered = !!(state.q || state.filters.warehouse_id || state.filters.product_id);

  return (
    <div className="stack">
      <div className="page-head"><h1>{t.stock.movementsTitle}</h1></div>
      <StockTabs />
      <div className="toolbar">
        <input type="search" value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder={t.stock.searchPlaceholder} aria-label={t.list.search} maxLength={100} />
        <select aria-label={t.stock.colWarehouse} value={state.filters.warehouse_id} onChange={(e) => update({ filters: { warehouse_id: e.target.value } })}>
          <option value="">{t.stock.allWarehouses}</option>
          {warehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
        {filtered ? <Button variant="secondary" onClick={() => { setSearchText(""); update({ q: "", filters: { warehouse_id: "", product_id: "" } }); }}>{t.list.reset}</Button> : null}
      </div>
      <DataTable
        caption={t.stock.movementsTitle} columns={columns} rows={data?.items ?? null} total={data?.total ?? 0}
        page={state.page} pageSize={data?.page_size ?? 25} loading={loading} error={error} onRetry={load}
        sort="created_at" order="desc" onSort={() => undefined} onPage={(p) => update({ page: p })} rowKey={(m) => m.id}
        empty={filtered ? <p className="muted">{t.list.emptyFiltered}</p> : <p className="muted">{t.stock.emptyMovements}</p>}
      />
    </div>
  );
}
