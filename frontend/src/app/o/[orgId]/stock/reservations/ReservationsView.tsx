"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { StockTabs } from "@/components/StockTabs";
import { Banner, Button, ConfirmDialog, Skeleton } from "@/components/ui";
import { errorText } from "@/i18n/errors";
import { useFormat, useLocale, useT } from "@/i18n/LocaleProvider";
import { pickName } from "@/i18n/names";
import { api, toApiError, type ApiError, type Page, type StockReservation } from "@/lib/api";
import { formatQuantity } from "@/lib/money";
import { useSession } from "@/lib/session";
import { useListParams } from "@/lib/useListParams";

export function ReservationsView() {
  const t = useT();
  const { locale } = useLocale();
  const { dateTime } = useFormat();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("stock.read");
  const canManage = !!org?.permissions.includes("stock.manage");

  const { state, update } = useListParams({ sort: "created_at", order: "desc", filters: { status: "ACTIVE" } });
  const [searchText, setSearchText] = useState(state.q);
  const [data, setData] = useState<Page<StockReservation> | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [releasing, setReleasing] = useState<StockReservation | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (searchText === state.q) return;
    const id = setTimeout(() => update({ q: searchText }), 300);
    return () => clearTimeout(id);
  }, [searchText, state.q, update]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const qs = new URLSearchParams({ page: String(state.page), page_size: "25", status: state.filters.status });
    if (state.q) qs.set("q", state.q);
    try {
      setData(await api<Page<StockReservation>>(`/organizations/${orgId}/stock/reservations?${qs}`));
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

  async function release() {
    if (!releasing) return;
    setBusy(true);
    setActionError(null);
    try {
      await api(`/organizations/${orgId}/stock/reservations/${releasing.id}/release`, { method: "POST" });
      setReleasing(null);
      await load();
    } catch (e) {
      setActionError(toApiError(e));
      setReleasing(null);
    } finally {
      setBusy(false);
    }
  }

  const columns: Column<StockReservation>[] = [
    { key: "wh", header: t.stock.colWarehouse, render: (r) => r.warehouse.name },
    { key: "sku", header: t.stock.colSku, render: (r) => r.sku },
    { key: "name", header: t.stock.colProduct, render: (r) => r.name },
    { key: "qty", header: t.stock.colQty, render: (r) => <span className="num">{formatQuantity(r.quantity, locale)} {pickName(r.unit, locale) || r.unit.code}</span> },
    { key: "reason", header: t.stock.colReason, render: (r) => r.reason ?? "—" },
    { key: "created", header: t.stock.colCreated, render: (r) => dateTime(r.created_at) },
    {
      key: "status", header: t.stock.colStatus,
      render: (r) => (
        r.status === "ACTIVE" ? (
          <>
            <span className="badge badge-warn">{t.stock.reservationStatuses.ACTIVE}</span>{" "}
            {canManage ? <Button variant="secondary" className="btn-sm" onClick={() => setReleasing(r)}>{t.stock.release}</Button> : null}
          </>
        ) : <span className="badge badge-muted">{t.stock.reservationStatuses.RELEASED}</span>
      ),
    },
  ];
  const filtered = !!(state.q || state.filters.status !== "ACTIVE");

  return (
    <div className="stack">
      <div className="page-head"><h1>{t.stock.reservationsTitle}</h1></div>
      <StockTabs />
      <p className="hint">{t.stock.reserveHint}</p>
      {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}
      <div className="toolbar">
        <input type="search" value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder={t.stock.searchPlaceholder} aria-label={t.list.search} maxLength={100} />
        <select aria-label={t.stock.colStatus} value={state.filters.status} onChange={(e) => update({ filters: { status: e.target.value } })}>
          {Object.entries(t.stock.reservationStatuses).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        {filtered ? <Button variant="secondary" onClick={() => { setSearchText(""); update({ q: "", filters: { status: "ACTIVE" } }); }}>{t.list.reset}</Button> : null}
      </div>
      <DataTable
        caption={t.stock.reservationsTitle} columns={columns} rows={data?.items ?? null} total={data?.total ?? 0}
        page={state.page} pageSize={data?.page_size ?? 25} loading={loading} error={error} onRetry={load}
        sort="created_at" order="desc" onSort={() => undefined} onPage={(p) => update({ page: p })} rowKey={(r) => r.id}
        empty={filtered ? <p className="muted">{t.list.emptyFiltered}</p> : <p className="muted">{t.stock.emptyReservations}</p>}
      />
      <ConfirmDialog open={!!releasing} title={t.stock.releaseTitle} body={t.stock.releaseBody} confirmLabel={t.stock.release} busy={busy} onCancel={() => setReleasing(null)} onConfirm={() => void release()} />
    </div>
  );
}
