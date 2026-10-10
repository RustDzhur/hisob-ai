"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { StockTabs } from "@/components/StockTabs";
import { Banner, Button, Skeleton } from "@/components/ui";
import { errorText, fill } from "@/i18n/errors";
import { useLocale, useT } from "@/i18n/LocaleProvider";
import { pickName } from "@/i18n/names";
import { api, toApiError, type ApiError, type Page, type Reconciliation, type StockBalance, type Warehouse } from "@/lib/api";
import { formatQuantity } from "@/lib/money";
import { useSession } from "@/lib/session";
import { useListParams } from "@/lib/useListParams";

export function StockView() {
  const t = useT();
  const { locale } = useLocale();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("stock.read");
  const canSettings = !!org?.permissions.includes("org.manage");
  const canManage = !!org?.permissions.includes("stock.manage");

  const { state, update } = useListParams({ sort: "name", order: "asc", filters: { warehouse_id: "", include_zero: "" } });
  const [searchText, setSearchText] = useState(state.q);
  const [data, setData] = useState<Page<StockBalance> | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [rec, setRec] = useState<Reconciliation | null>(null);
  const [policy, setPolicy] = useState<"forbid" | "allow" | null>(null);
  const [policyMsg, setPolicyMsg] = useState<"saved" | ApiError | null>(null);

  useEffect(() => {
    if (searchText === state.q) return;
    const id = setTimeout(() => update({ q: searchText }), 300);
    return () => clearTimeout(id);
  }, [searchText, state.q, update]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const qs = new URLSearchParams({ page: String(state.page), page_size: "25", sort: state.sort, order: state.order });
    if (state.filters.warehouse_id) qs.set("warehouse_id", state.filters.warehouse_id);
    if (state.filters.include_zero) qs.set("include_zero", "true");
    if (state.q) qs.set("q", state.q);
    try {
      setData(await api<Page<StockBalance>>(`/organizations/${orgId}/stock/balances?${qs}`));
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
    if (!canRead) return;
    api<Page<Warehouse>>(`/organizations/${orgId}/warehouses?page_size=100&status=all`).then((p) => setWarehouses(p.items)).catch(() => setWarehouses([]));
    api<Reconciliation>(`/organizations/${orgId}/stock/reconciliation`).then(setRec).catch(() => setRec(null));
    api<{ negative_stock_policy: "forbid" | "allow" }>(`/organizations/${orgId}/stock/settings`).then((s) => setPolicy(s.negative_stock_policy)).catch(() => setPolicy(null));
  }, [canRead, orgId, data]);

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) {
    return (
      <div className="card">
        <h1>{t.catalog.noAccessTitle}</h1>
        <p className="muted">{t.stock.noAccessBody}</p>
      </div>
    );
  }

  async function savePolicy(v: "forbid" | "allow") {
    setPolicyMsg(null);
    try {
      await api(`/organizations/${orgId}/stock/settings`, { method: "PUT", body: { negative_stock_policy: v } });
      setPolicy(v);
      setPolicyMsg("saved");
    } catch (e) {
      setPolicyMsg(toApiError(e));
    }
  }

  const columns: Column<StockBalance>[] = [
    { key: "wh", header: t.stock.colWarehouse, sortKey: "warehouse", render: (b) => b.warehouse.name },
    { key: "sku", header: t.stock.colSku, sortKey: "sku", render: (b) => b.sku },
    { key: "name", header: t.stock.colProduct, sortKey: "name", render: (b) => b.name },
    {
      key: "qty", header: t.stock.colQty, sortKey: "quantity",
      render: (b) => <span className={`num${b.quantity.startsWith("-") ? " qty-neg" : ""}`}>{formatQuantity(b.quantity, locale)}</span>,
    },
    { key: "unit", header: t.stock.colUnit, render: (b) => pickName(b.unit, locale) || b.unit.code },
    { key: "hist", header: "", render: (b) => <Link href={`/o/${orgId}/stock/movements?product_id=${b.product_id}&warehouse_id=${b.warehouse.id}`}>{t.stock.history}</Link> },
  ];
  const filtered = !!(state.q || state.filters.warehouse_id || state.filters.include_zero);

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{t.stock.balancesTitle}</h1>
        {canManage ? <Link className="btn" href={`/o/${orgId}/stock/operations/new`}>{t.stock.newOperation}</Link> : null}
      </div>
      <StockTabs />
      <p className="hint">{t.stock.note}</p>

      {rec ? (
        rec.ok ? (
          <Banner kind="info">{fill(t.stock.reconcileOk, { n: rec.checked_pairs })}</Banner>
        ) : (
          <Banner>
            {fill(t.stock.reconcileBad, { n: rec.mismatches.length })}
            <ul>
              {rec.mismatches.slice(0, 5).map((m) => (
                <li key={m.warehouse.id + m.product_id}>{m.warehouse.name} · {m.sku}: {t.stock.reconcileBalance} {m.balance}, {t.stock.reconcileSum} {m.movements_sum}</li>
              ))}
            </ul>
          </Banner>
        )
      ) : null}

      <div className="toolbar">
        <input type="search" value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder={t.stock.searchPlaceholder} aria-label={t.list.search} maxLength={100} />
        <select aria-label={t.stock.colWarehouse} value={state.filters.warehouse_id} onChange={(e) => update({ filters: { warehouse_id: e.target.value } })}>
          <option value="">{t.stock.allWarehouses}</option>
          {warehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
        <label className="ack-row" style={{ margin: 0 }}>
          <input type="checkbox" checked={!!state.filters.include_zero} onChange={(e) => update({ filters: { include_zero: e.target.checked ? "1" : "" } })} />
          <span>{t.stock.showZero}</span>
        </label>
        {filtered ? <Button variant="secondary" onClick={() => { setSearchText(""); update({ q: "", filters: { warehouse_id: "", include_zero: "" } }); }}>{t.list.reset}</Button> : null}
      </div>

      <DataTable
        caption={t.stock.balancesTitle} columns={columns} rows={data?.items ?? null} total={data?.total ?? 0}
        page={state.page} pageSize={data?.page_size ?? 25} loading={loading} error={error} onRetry={load}
        sort={state.sort} order={state.order}
        onSort={(key, order) => update({ sort: key, order: order ?? (state.sort === key && state.order === "asc" ? "desc" : "asc") })}
        onPage={(p) => update({ page: p })} rowKey={(b) => b.warehouse.id + b.product_id}
        empty={filtered ? <p className="muted">{t.list.emptyFiltered}</p> : <p className="muted">{t.stock.emptyBalances}</p>}
      />

      {canSettings && policy ? (
        <section className="card" aria-labelledby="pol-h">
          <h2 id="pol-h">{t.stock.policyTitle}</h2>
          <div className="toolbar">
            <select aria-label={t.stock.policyTitle} value={policy} onChange={(e) => void savePolicy(e.target.value as "forbid" | "allow")}>
              <option value="forbid">{t.stock.policyForbid}</option>
              <option value="allow">{t.stock.policyAllow}</option>
            </select>
            {policyMsg === "saved" ? <span className="hint" role="status">{t.stock.policySaved}</span> : null}
          </div>
          {policyMsg && policyMsg !== "saved" ? <Banner>{errorText(t, policyMsg)}</Banner> : null}
        </section>
      ) : null}
    </div>
  );
}
