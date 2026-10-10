"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AccountingTabs } from "@/components/AccountingTabs";
import { DataTable, type Column } from "@/components/DataTable";
import { EntityPicker } from "@/components/EntityPicker";
import { Banner, Button, ConfirmDialog, Drawer, Field, Skeleton, TextArea } from "@/components/ui";
import { errorText, fieldErrorMap } from "@/i18n/errors";
import { useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type Account, type ApiError, type Page } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useListParams } from "@/lib/useListParams";

type Form = { code: string; name: string; parent_id: string; parent_label: string; allow_posting: boolean; notes: string };
const emptyForm: Form = { code: "", name: "", parent_id: "", parent_label: "", allow_posting: true, notes: "" };

export function AccountsView() {
  const t = useT();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("accounting.read");
  const canManage = !!org?.permissions.includes("accounting.manage");

  const { state, update } = useListParams({ sort: "code", order: "asc", filters: { status: "active" } });
  const [searchText, setSearchText] = useState(state.q);
  const [data, setData] = useState<Page<Account> | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [drawer, setDrawer] = useState<"new" | Account | null>(null);
  const [form, setForm] = useState<Form>(emptyForm);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [archiving, setArchiving] = useState<Account | null>(null);

  useEffect(() => {
    if (searchText === state.q) return;
    const id = setTimeout(() => update({ q: searchText }), 300);
    return () => clearTimeout(id);
  }, [searchText, state.q, update]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const qs = new URLSearchParams({ page: String(state.page), page_size: "25", sort: state.sort, order: state.order, status: state.filters.status });
    if (state.q) qs.set("q", state.q);
    try {
      setData(await api<Page<Account>>(`/organizations/${orgId}/accounting/accounts?${qs}`));
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
  if (!canRead) return <div className="card"><h1>{t.catalog.noAccessTitle}</h1><p className="muted">{t.accounting.noAccessBody}</p></div>;

  function openDrawer(a: "new" | Account) {
    setErrors({});
    setActionError(null);
    setForm(a === "new" ? emptyForm : { code: a.code, name: a.name, parent_id: a.parent_id ?? "", parent_label: a.parent_code ?? "", allow_posting: a.allow_posting, notes: a.notes ?? "" });
    setDrawer(a);
  }

  async function save() {
    if (!drawer) return;
    setBusy(true);
    setErrors({});
    try {
      const body = { code: form.code, name: form.name, parent_id: form.parent_id || null, allow_posting: form.allow_posting, notes: form.notes || null };
      if (drawer === "new") await api(`/organizations/${orgId}/accounting/accounts`, { method: "POST", body });
      else await api(`/organizations/${orgId}/accounting/accounts/${drawer.id}`, { method: "PATCH", body });
      setDrawer(null);
      await load();
    } catch (e) {
      const fe = fieldErrorMap(t, e);
      if (Object.keys(fe).length) setErrors(fe);
      else setActionError(toApiError(e));
    } finally {
      setBusy(false);
    }
  }

  async function setArchived(a: Account, archived: boolean) {
    setActionError(null);
    try {
      await api(`/organizations/${orgId}/accounting/accounts/${a.id}/${archived ? "archive" : "restore"}`, { method: "POST" });
      await load();
    } catch (e) {
      setActionError(toApiError(e));
    } finally {
      setArchiving(null);
    }
  }

  const searchParents = async (q: string) => (await api<Page<Account>>(`/organizations/${orgId}/accounting/accounts?page_size=10&q=${encodeURIComponent(q)}`)).items;
  const columns: Column<Account>[] = [
    { key: "code", header: t.accounting.colCode, sortKey: "code", render: (a) => <Link href={`/o/${orgId}/accounting/accounts/${a.id}`}><strong>{a.code}</strong></Link> },
    {
      key: "name", header: t.accounting.colName, sortKey: "name",
      render: (a) => (
        <>
          {a.name}
          {!a.allow_posting ? <> <span className="badge badge-info">{t.accounting.groupAccount}</span></> : null}
          {a.archived ? <> <span className="badge badge-muted">{t.accounting.archived}</span></> : null}
        </>
      ),
    },
    { key: "parent", header: t.accounting.colParent, render: (a) => a.parent_code ?? "—" },
    {
      key: "act", header: "",
      render: (a) => canManage ? (
        <span className="row-actions">
          {!a.archived ? <Button variant="secondary" className="btn-sm" onClick={() => openDrawer(a)}>{t.accounting.edit}</Button> : null}
          {!a.archived ? <Button variant="secondary" className="btn-sm" onClick={() => setArchiving(a)}>{t.accounting.archive}</Button> : <Button variant="secondary" className="btn-sm" onClick={() => void setArchived(a, false)}>{t.accounting.restore}</Button>}
        </span>
      ) : null,
    },
  ];
  const filtered = !!(state.q || state.filters.status !== "active");

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{t.accounting.accountsTitle}</h1>
        {canManage ? <Button onClick={() => openDrawer("new")}>{t.accounting.newAccount}</Button> : null}
      </div>
      <AccountingTabs />
      {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}
      <div className="toolbar">
        <input type="search" value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder={t.accounting.searchPlaceholder} aria-label={t.list.search} maxLength={100} />
        <select aria-label={t.accounting.colStatus} value={state.filters.status} onChange={(e) => update({ filters: { status: e.target.value } })}>
          {Object.entries(t.accounting.statusFilters).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        {filtered ? <Button variant="secondary" onClick={() => { setSearchText(""); update({ q: "", filters: { status: "active" } }); }}>{t.list.reset}</Button> : null}
      </div>
      <DataTable
        caption={t.accounting.accountsTitle} columns={columns} rows={data?.items ?? null} total={data?.total ?? 0}
        page={state.page} pageSize={data?.page_size ?? 25} loading={loading} error={error} onRetry={load}
        sort={state.sort} order={state.order}
        onSort={(key, order) => update({ sort: key, order: order ?? (state.sort === key && state.order === "asc" ? "desc" : "asc") })}
        onPage={(p) => update({ page: p })} rowKey={(a) => a.id}
        empty={filtered ? <p className="muted">{t.list.emptyFiltered}</p> : (
          <>
            <p className="muted">{t.accounting.emptyAccounts}</p>
            {canManage ? <Button onClick={() => openDrawer("new")}>{t.accounting.newAccount}</Button> : null}
          </>
        )}
      />

      <Drawer open={!!drawer} title={drawer === "new" ? t.accounting.newAccount : t.accounting.edit} onClose={() => !busy && setDrawer(null)}>
        <Field id="a-code" label={t.accounting.fCode} value={form.code} maxLength={32} onChange={(e) => setForm({ ...form, code: e.target.value })} error={errors.code} />
        <Field id="a-name" label={t.accounting.fName} value={form.name} maxLength={300} onChange={(e) => setForm({ ...form, name: e.target.value })} error={errors.name} />
        <EntityPicker<Account> label={t.accounting.fParent} value={form.parent_id ? { id: form.parent_id, label: form.parent_label } : null}
          search={searchParents} toItem={(a) => ({ id: a.id, label: a.code, hint: a.name })}
          onChange={(it) => setForm({ ...form, parent_id: it?.id ?? "", parent_label: it?.label ?? "" })} error={errors.parent_id} />
        <label className="ack-row">
          <input type="checkbox" checked={form.allow_posting} onChange={(e) => setForm({ ...form, allow_posting: e.target.checked })} />
          <span>{t.accounting.fAllowPosting}</span>
        </label>
        <TextArea id="a-notes" label={t.accounting.fNotes} value={form.notes} maxLength={2000} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
        {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}
        <div className="drawer-actions">
          <Button onClick={() => void save()} loading={busy} disabled={!form.code.trim() || !form.name.trim()}>{t.accounting.save}</Button>
          <Button variant="secondary" onClick={() => setDrawer(null)} disabled={busy}>{t.accounting.cancel}</Button>
        </div>
      </Drawer>
      <ConfirmDialog open={!!archiving} title={t.accounting.archiveTitle} body={t.accounting.archiveBody} confirmLabel={t.accounting.archive}
        onCancel={() => setArchiving(null)} onConfirm={() => archiving && void setArchived(archiving, true)} />
    </div>
  );
}
