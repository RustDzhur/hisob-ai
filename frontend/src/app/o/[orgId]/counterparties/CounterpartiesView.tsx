"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { Banner, Button, Checkbox, ConfirmDialog, Drawer, Field, Skeleton, TextArea } from "@/components/ui";
import { errorText, fieldErrorMap } from "@/i18n/errors";
import { useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type Counterparty, type Page } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useListParams } from "@/lib/useListParams";

type Form = {
  name: string; tin: string; is_customer: boolean; is_supplier: boolean;
  address: string; phone: string; email: string; notes: string;
};
const emptyForm: Form = { name: "", tin: "", is_customer: false, is_supplier: false, address: "", phone: "", email: "", notes: "" };
const toForm = (c: Counterparty): Form => ({
  name: c.name, tin: c.tin ?? "", is_customer: c.is_customer, is_supplier: c.is_supplier,
  address: c.address ?? "", phone: c.phone ?? "", email: c.email ?? "", notes: c.notes ?? "",
});
const body = (f: Form) => ({
  name: f.name, tin: f.tin, is_customer: f.is_customer, is_supplier: f.is_supplier,
  address: f.address, phone: f.phone, email: f.email, notes: f.notes,
});

export function CounterpartiesView() {
  const t = useT();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("catalog.read");
  const canManage = !!org?.permissions.includes("catalog.manage");

  const { state, update } = useListParams({ sort: "name", order: "asc", filters: { role: "all", status: "active" } });
  const [searchText, setSearchText] = useState(state.q);
  const [data, setData] = useState<Page<Counterparty> | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState<ApiError | null>(null);

  const [drawer, setDrawer] = useState<"new" | Counterparty | null>(null);
  const [form, setForm] = useState<Form>(emptyForm);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formBusy, setFormBusy] = useState(false);
  const [archiving, setArchiving] = useState<Counterparty | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // поиск: отправляем на сервер через 300 мс после последнего символа
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
      setData(await api<Page<Counterparty>>(`/organizations/${orgId}/counterparties?${qs}`));
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
        <p className="muted">{t.catalog.noAccessBody}</p>
      </div>
    );
  }

  function openNew() { setForm(emptyForm); setErrors({}); setDrawer("new"); }
  function openEdit(c: Counterparty) { setForm(toForm(c)); setErrors({}); setDrawer(c); }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim()) { setErrors({ name: t.auth.required }); return; }
    setErrors({});
    setFormBusy(true);
    try {
      if (drawer === "new") await api(`/organizations/${orgId}/counterparties`, { method: "POST", body: body(form) });
      else if (drawer) await api(`/organizations/${orgId}/counterparties/${drawer.id}`, { method: "PATCH", body: body(form) });
      setDrawer(null);
      await load();
    } catch (err) {
      const fe = fieldErrorMap(t, err);
      setErrors(Object.keys(fe).length ? fe : { name: errorText(t, toApiError(err)) });
    } finally {
      setFormBusy(false);
    }
  }

  async function setArchived(c: Counterparty, archived: boolean) {
    setActionError(null);
    setBusyId(c.id);
    try {
      await api(`/organizations/${orgId}/counterparties/${c.id}/${archived ? "archive" : "restore"}`, { method: "POST" });
      await load();
    } catch (err) {
      setActionError(toApiError(err));
    } finally {
      setBusyId(null);
      setArchiving(null);
    }
  }

  const columns: Column<Counterparty>[] = [
    {
      key: "name", header: t.counterparties.colName, sortKey: "name",
      render: (c) => (<>{c.name}{c.archived ? <> <span className="badge badge-muted">{t.counterparties.archived}</span></> : null}{c.email ? <div className="sub">{c.email}</div> : null}</>),
    },
    { key: "tin", header: t.counterparties.colTin, sortKey: "tin", render: (c) => c.tin ?? "—" },
    {
      key: "roles", header: t.counterparties.colRoles,
      render: (c) => (<>{c.is_customer ? <span className="badge badge-ok">{t.counterparties.customer}</span> : null}{c.is_customer && c.is_supplier ? " " : null}{c.is_supplier ? <span className="badge badge-info">{t.counterparties.supplier}</span> : null}{!c.is_customer && !c.is_supplier ? "—" : null}</>),
    },
    { key: "phone", header: t.counterparties.colPhone, render: (c) => c.phone ?? "—" },
  ];
  if (canManage) {
    columns.push({
      key: "actions", header: t.counterparties.colActions,
      render: (c) => (
        <div className="row-actions">
          {!c.archived ? (
            <Button variant="secondary" className="btn-sm" aria-label={`${t.counterparties.edit}: ${c.name}`} onClick={() => openEdit(c)}>{t.counterparties.edit}</Button>
          ) : null}
          {c.archived ? (
            <Button variant="secondary" className="btn-sm" loading={busyId === c.id} aria-label={`${t.counterparties.restore}: ${c.name}`} onClick={() => void setArchived(c, false)}>{t.counterparties.restore}</Button>
          ) : (
            <Button variant="secondary" className="btn-sm" disabled={busyId === c.id} aria-label={`${t.counterparties.archive}: ${c.name}`} onClick={() => setArchiving(c)}>{t.counterparties.archive}</Button>
          )}
        </div>
      ),
    });
  }

  const filtered = !!(state.q || state.filters.role !== "all" || state.filters.status !== "active");

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{t.counterparties.title}</h1>
        {canManage ? <Button onClick={openNew}>{t.counterparties.add}</Button> : null}
      </div>

      <div className="toolbar">
        <input type="search" value={searchText} onChange={(e) => setSearchText(e.target.value)}
          placeholder={t.counterparties.searchPlaceholder} aria-label={t.list.search} maxLength={100} />
        <select aria-label={t.counterparties.filterRole} value={state.filters.role} onChange={(e) => update({ filters: { role: e.target.value } })}>
          <option value="all">{t.counterparties.roleAll}</option>
          <option value="customer">{t.counterparties.roleCustomers}</option>
          <option value="supplier">{t.counterparties.roleSuppliers}</option>
        </select>
        <select aria-label={t.counterparties.filterStatus} value={state.filters.status} onChange={(e) => update({ filters: { status: e.target.value } })}>
          <option value="active">{t.counterparties.statusActive}</option>
          <option value="archived">{t.counterparties.statusArchived}</option>
          <option value="all">{t.counterparties.statusAll}</option>
        </select>
        {filtered ? (
          <Button variant="secondary" onClick={() => { setSearchText(""); update({ q: "", filters: { role: "all", status: "active" } }); }}>{t.list.reset}</Button>
        ) : null}
      </div>

      {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}

      <DataTable
        caption={t.counterparties.title}
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
        rowKey={(c) => c.id}
        empty={
          filtered ? (
            <p className="muted">{t.list.emptyFiltered}</p>
          ) : (
            <>
              <p className="muted">{t.counterparties.empty}</p>
              {canManage ? <Button onClick={openNew}>{t.counterparties.add}</Button> : null}
            </>
          )
        }
      />

      <Drawer open={drawer !== null} title={drawer === "new" ? t.counterparties.newTitle : t.counterparties.editTitle} onClose={() => setDrawer(null)}>
        <form onSubmit={save} noValidate>
          <Field id="cp-name" label={t.counterparties.fName} value={form.name} maxLength={200} onChange={(e) => setForm({ ...form, name: e.target.value })} error={errors.name} />
          <Field id="cp-tin" label={t.counterparties.fTin} inputMode="numeric" value={form.tin} onChange={(e) => setForm({ ...form, tin: e.target.value })} error={errors.tin} hint={t.onboarding.tinHint} />
          <Checkbox id="cp-customer" label={t.counterparties.customer} checked={form.is_customer} onChange={(e) => setForm({ ...form, is_customer: e.target.checked })} />
          <Checkbox id="cp-supplier" label={t.counterparties.supplier} checked={form.is_supplier} onChange={(e) => setForm({ ...form, is_supplier: e.target.checked })} />
          <div style={{ height: 8 }} />
          <Field id="cp-address" label={t.counterparties.fAddress} value={form.address} maxLength={500} onChange={(e) => setForm({ ...form, address: e.target.value })} error={errors.address} />
          <Field id="cp-phone" label={t.counterparties.fPhone} type="tel" value={form.phone} maxLength={32} onChange={(e) => setForm({ ...form, phone: e.target.value })} error={errors.phone} />
          <Field id="cp-email" label={t.counterparties.fEmail} type="email" value={form.email} maxLength={200} onChange={(e) => setForm({ ...form, email: e.target.value })} error={errors.email} />
          <TextArea id="cp-notes" label={t.counterparties.fNotes} value={form.notes} maxLength={2000} onChange={(e) => setForm({ ...form, notes: e.target.value })} error={errors.notes} />
          <div className="drawer-actions">
            <Button type="submit" loading={formBusy}>{t.catalog.saveNames}</Button>
            <Button type="button" variant="secondary" onClick={() => setDrawer(null)}>{t.catalog.cancel}</Button>
          </div>
        </form>
      </Drawer>

      <ConfirmDialog
        open={archiving !== null}
        title={t.counterparties.archiveTitle}
        body={t.counterparties.archiveBody}
        confirmLabel={t.catalog.confirm}
        busy={busyId !== null}
        onCancel={() => setArchiving(null)}
        onConfirm={() => archiving && void setArchived(archiving, true)}
      />
    </div>
  );
}
