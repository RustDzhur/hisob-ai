"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { Banner, Button, ConfirmDialog, Drawer, Field, Skeleton, TextArea } from "@/components/ui";
import { errorText, fieldErrorMap } from "@/i18n/errors";
import { useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type Page, type Warehouse } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useListParams } from "@/lib/useListParams";

type Form = { name: string; address: string; notes: string };
const emptyForm: Form = { name: "", address: "", notes: "" };

export function WarehousesView() {
  const t = useT();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("warehouses.read");
  const canManage = !!org?.permissions.includes("warehouses.manage");

  const { state, update } = useListParams({ sort: "name", order: "asc", filters: { status: "active" } });
  const [searchText, setSearchText] = useState(state.q);
  const [data, setData] = useState<Page<Warehouse> | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState<ApiError | null>(null);

  const [drawer, setDrawer] = useState<"new" | Warehouse | null>(null);
  const [form, setForm] = useState<Form>(emptyForm);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formBusy, setFormBusy] = useState(false);
  const [archiving, setArchiving] = useState<Warehouse | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    if (searchText === state.q) return;
    const id = setTimeout(() => update({ q: searchText }), 300);
    return () => clearTimeout(id);
  }, [searchText, state.q, update]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const qs = new URLSearchParams({ page: String(state.page), page_size: "25", sort: state.sort, order: state.order, ...state.filters });
    if (state.q) qs.set("q", state.q);
    try {
      setData(await api<Page<Warehouse>>(`/organizations/${orgId}/warehouses?${qs}`));
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
        <p className="muted">{t.warehouses.noAccessBody}</p>
      </div>
    );
  }

  function openNew() { setForm(emptyForm); setErrors({}); setDrawer("new"); }
  function openEdit(w: Warehouse) { setForm({ name: w.name, address: w.address ?? "", notes: w.notes ?? "" }); setErrors({}); setDrawer(w); }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim()) { setErrors({ name: t.auth.required }); return; }
    setErrors({});
    setFormBusy(true);
    try {
      if (drawer === "new") await api(`/organizations/${orgId}/warehouses`, { method: "POST", body: form });
      else if (drawer) await api(`/organizations/${orgId}/warehouses/${drawer.id}`, { method: "PATCH", body: form });
      setDrawer(null);
      await load();
    } catch (err) {
      const fe = fieldErrorMap(t, err);
      setErrors(Object.keys(fe).length ? fe : { name: errorText(t, toApiError(err)) });
    } finally {
      setFormBusy(false);
    }
  }

  async function setArchived(w: Warehouse, archived: boolean) {
    setActionError(null);
    setBusyId(w.id);
    try {
      await api(`/organizations/${orgId}/warehouses/${w.id}/${archived ? "archive" : "restore"}`, { method: "POST" });
      await load();
    } catch (err) {
      setActionError(toApiError(err));
    } finally {
      setBusyId(null);
      setArchiving(null);
    }
  }

  const columns: Column<Warehouse>[] = [
    {
      key: "name", header: t.warehouses.colName, sortKey: "name",
      render: (w) => (<>{w.name}{w.archived ? <> <span className="badge badge-muted">{t.warehouses.archived}</span></> : null}{w.notes ? <div className="sub">{w.notes}</div> : null}</>),
    },
    { key: "address", header: t.warehouses.colAddress, render: (w) => w.address ?? "—" },
  ];
  if (canManage) {
    columns.push({
      key: "actions", header: t.warehouses.colActions,
      render: (w) => (
        <div className="row-actions">
          {!w.archived ? <Button variant="secondary" className="btn-sm" aria-label={`${t.warehouses.edit}: ${w.name}`} onClick={() => openEdit(w)}>{t.warehouses.edit}</Button> : null}
          {w.archived ? (
            <Button variant="secondary" className="btn-sm" loading={busyId === w.id} aria-label={`${t.warehouses.restore}: ${w.name}`} onClick={() => void setArchived(w, false)}>{t.warehouses.restore}</Button>
          ) : (
            <Button variant="secondary" className="btn-sm" disabled={busyId === w.id} aria-label={`${t.warehouses.archive}: ${w.name}`} onClick={() => setArchiving(w)}>{t.warehouses.archive}</Button>
          )}
        </div>
      ),
    });
  }

  const filtered = !!(state.q || state.filters.status !== "active");

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{t.warehouses.title}</h1>
        {canManage ? <Button onClick={openNew}>{t.warehouses.add}</Button> : null}
      </div>
      <div className="toolbar">
        <input type="search" value={searchText} onChange={(e) => setSearchText(e.target.value)}
          placeholder={t.warehouses.searchPlaceholder} aria-label={t.list.search} maxLength={100} />
        <select aria-label={t.warehouses.filterStatus} value={state.filters.status} onChange={(e) => update({ filters: { status: e.target.value } })}>
          <option value="active">{t.warehouses.statusActive}</option>
          <option value="archived">{t.warehouses.statusArchived}</option>
          <option value="all">{t.warehouses.statusAll}</option>
        </select>
        {filtered ? (
          <Button variant="secondary" onClick={() => { setSearchText(""); update({ q: "", filters: { status: "active" } }); }}>{t.list.reset}</Button>
        ) : null}
      </div>

      {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}

      <DataTable
        caption={t.warehouses.title}
        columns={columns}
        rows={data?.items ?? null}
        total={data?.total ?? 0}
        page={state.page}
        pageSize={data?.page_size ?? 25}
        loading={loading}
        error={error}
        onRetry={load}
        sort={state.sort}
        order={state.order}
        onSort={(key, order) => update({ sort: key, order: order ?? (state.sort === key && state.order === "asc" ? "desc" : "asc") })}
        onPage={(p) => update({ page: p })}
        rowKey={(w) => w.id}
        empty={
          filtered ? (
            <p className="muted">{t.list.emptyFiltered}</p>
          ) : (
            <>
              <p className="muted">{t.warehouses.empty}</p>
              {canManage ? <Button onClick={openNew}>{t.warehouses.add}</Button> : null}
            </>
          )
        }
      />

      <Drawer open={drawer !== null} title={drawer === "new" ? t.warehouses.newTitle : t.warehouses.editTitle} onClose={() => setDrawer(null)}>
        <form onSubmit={save} noValidate>
          <Field id="wh-name" label={t.warehouses.fName} value={form.name} maxLength={200} onChange={(e) => setForm({ ...form, name: e.target.value })} error={errors.name} />
          <Field id="wh-address" label={t.warehouses.fAddress} value={form.address} maxLength={500} onChange={(e) => setForm({ ...form, address: e.target.value })} error={errors.address} />
          <TextArea id="wh-notes" label={t.warehouses.fNotes} value={form.notes} maxLength={2000} onChange={(e) => setForm({ ...form, notes: e.target.value })} error={errors.notes} />
          <div className="drawer-actions">
            <Button type="submit" loading={formBusy}>{t.catalog.saveNames}</Button>
            <Button type="button" variant="secondary" onClick={() => setDrawer(null)}>{t.catalog.cancel}</Button>
          </div>
        </form>
      </Drawer>

      <ConfirmDialog
        open={archiving !== null}
        title={t.warehouses.archiveTitle}
        body={t.warehouses.archiveBody}
        confirmLabel={t.catalog.confirm}
        busy={busyId !== null}
        onCancel={() => setArchiving(null)}
        onConfirm={() => archiving && void setArchived(archiving, true)}
      />
    </div>
  );
}
