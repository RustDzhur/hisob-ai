"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { StockTabs } from "@/components/StockTabs";
import { Banner, Button, Modal, Skeleton } from "@/components/ui";
import { errorText } from "@/i18n/errors";
import { useFormat, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type Page, type StockCount, type StockCountItem, type Warehouse } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useListParams } from "@/lib/useListParams";

export function CountsView() {
  const t = useT();
  const { dateTime } = useFormat();
  const router = useRouter();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("stock.read");
  const canManage = !!org?.permissions.includes("stock.manage");

  const { state, update } = useListParams({ sort: "created_at", order: "desc", filters: { status: "all" } });
  const [data, setData] = useState<Page<StockCountItem> | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [warehouseId, setWarehouseId] = useState("");
  const [busy, setBusy] = useState(false);
  const [startError, setStartError] = useState<ApiError | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const qs = new URLSearchParams({ page: String(state.page), page_size: "25", status: state.filters.status });
    try {
      setData(await api<Page<StockCountItem>>(`/organizations/${orgId}/stock/counts?${qs}`));
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

  async function openDialog() {
    setStartError(null);
    setOpen(true);
    if (!warehouses.length) {
      try {
        setWarehouses((await api<Page<Warehouse>>(`/organizations/${orgId}/warehouses?page_size=100&status=active`)).items);
      } catch {
        setWarehouses([]);
      }
    }
  }

  async function start() {
    setBusy(true);
    setStartError(null);
    try {
      const c = await api<StockCount>(`/organizations/${orgId}/stock/counts`, { method: "POST", body: { warehouse_id: warehouseId } });
      router.push(`/o/${orgId}/stock/counts/${c.id}`);
    } catch (e) {
      setStartError(toApiError(e));
      setBusy(false);
    }
  }

  const columns: Column<StockCountItem>[] = [
    { key: "created", header: t.stock.colCreated, render: (c) => <Link href={`/o/${orgId}/stock/counts/${c.id}`}>{dateTime(c.created_at)}</Link> },
    { key: "wh", header: t.stock.colWarehouse, render: (c) => c.warehouse_name },
    { key: "lines", header: t.stock.colLines, render: (c) => c.lines_count },
    { key: "counted", header: t.stock.colCounted, render: (c) => `${c.counted_count} / ${c.lines_count}` },
    { key: "status", header: t.stock.colStatus, render: (c) => <span className={`badge ${c.status === "POSTED" ? "badge-ok" : "badge-warn"}`}>{t.stock.countStatuses[c.status] ?? c.status}</span> },
  ];
  const filtered = state.filters.status !== "all";

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{t.stock.countsTitle}</h1>
        {canManage ? <Button onClick={() => void openDialog()}>{t.stock.startCount}</Button> : null}
      </div>
      <StockTabs />
      <div className="toolbar">
        <select aria-label={t.stock.colStatus} value={state.filters.status} onChange={(e) => update({ filters: { status: e.target.value } })}>
          {Object.entries(t.stock.countStatusFilters).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        {filtered ? <Button variant="secondary" onClick={() => update({ filters: { status: "all" } })}>{t.list.reset}</Button> : null}
      </div>
      <DataTable
        caption={t.stock.countsTitle} columns={columns} rows={data?.items ?? null} total={data?.total ?? 0}
        page={state.page} pageSize={data?.page_size ?? 25} loading={loading} error={error} onRetry={load}
        sort="created_at" order="desc" onSort={() => undefined} onPage={(p) => update({ page: p })} rowKey={(c) => c.id}
        empty={filtered ? <p className="muted">{t.list.emptyFiltered}</p> : <p className="muted">{t.stock.emptyCounts}</p>}
      />
      <Modal open={open} title={t.stock.startCountTitle} onClose={() => !busy && setOpen(false)}
        footer={<>
          <Button variant="secondary" onClick={() => setOpen(false)} disabled={busy}>{t.stock.cancel}</Button>
          <Button loading={busy} disabled={!warehouseId} onClick={() => void start()}>{t.stock.startCount}</Button>
        </>}>
        <p className="muted">{t.stock.startCountBody}</p>
        <div className="field">
          <label htmlFor="cnt-wh">{t.stock.fWarehouse}</label>
          <select id="cnt-wh" value={warehouseId} onChange={(e) => setWarehouseId(e.target.value)}>
            <option value="">—</option>
            {warehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </div>
        {startError ? <Banner>{errorText(t, startError)}</Banner> : null}
      </Modal>
    </div>
  );
}
