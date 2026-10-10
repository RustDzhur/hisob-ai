"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { Banner, Button, ConfirmDialog, Drawer, Field, Skeleton, TextArea } from "@/components/ui";
import { errorText, fieldErrorMap } from "@/i18n/errors";
import { useLocale, useT } from "@/i18n/LocaleProvider";
import { pickName } from "@/i18n/names";
import { api, toApiError, type ApiError, type Currency, type Page, type Product, type Unit } from "@/lib/api";
import { formatMoney, normalizeMoneyInput } from "@/lib/money";
import { useSession } from "@/lib/session";
import { useListParams } from "@/lib/useListParams";

type Form = {
  kind: "goods" | "service"; sku: string; name: string; unit_id: string; price: string;
  price_currency: string; ikpu: string; barcodes: string; description: string;
};
const emptyForm: Form = { kind: "goods", sku: "", name: "", unit_id: "", price: "", price_currency: "", ikpu: "", barcodes: "", description: "" };
const toForm = (p: Product): Form => ({
  kind: p.kind, sku: p.sku, name: p.name, unit_id: p.unit.id, price: p.price ?? "",
  price_currency: p.price_currency ?? "", ikpu: p.ikpu ?? "", barcodes: p.barcodes.join("\n"), description: p.description ?? "",
});
function toBody(f: Form) {
  const price = normalizeMoneyInput(f.price);
  return {
    kind: f.kind, sku: f.sku, name: f.name, unit_id: f.unit_id,
    price: price || null,
    // валюта без цены не отправляется: цена и валюта задаются только вместе
    price_currency: price ? f.price_currency || null : null,
    ikpu: f.ikpu, description: f.description,
    barcodes: f.barcodes.split("\n").map((s) => s.trim()).filter(Boolean),
  };
}

export function ProductsView() {
  const t = useT();
  const { locale } = useLocale();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("catalog.read");
  const canManage = !!org?.permissions.includes("catalog.manage");

  const { state, update } = useListParams({ sort: "name", order: "asc", filters: { kind: "all", status: "active" } });
  const [searchText, setSearchText] = useState(state.q);
  const [data, setData] = useState<Page<Product> | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState<ApiError | null>(null);

  const [units, setUnits] = useState<Unit[]>([]);
  const [currencies, setCurrencies] = useState<Currency[]>([]);
  const [drawer, setDrawer] = useState<"new" | Product | null>(null);
  const [form, setForm] = useState<Form>(emptyForm);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formBusy, setFormBusy] = useState(false);
  const [archiving, setArchiving] = useState<Product | null>(null);
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
      setData(await api<Page<Product>>(`/organizations/${orgId}/products?${qs}`));
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setLoading(false);
    }
  }, [orgId, state]);

  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);

  // справочники для формы подгружаем только тем, кто может менять товары
  useEffect(() => {
    if (!canManage) return;
    api<Unit[]>(`/organizations/${orgId}/units`).then(setUnits).catch(() => setUnits([]));
    api<Currency[]>(`/organizations/${orgId}/currencies`).then(setCurrencies).catch(() => setCurrencies([]));
  }, [canManage, orgId]);

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) {
    return (
      <div className="card">
        <h1>{t.catalog.noAccessTitle}</h1>
        <p className="muted">{t.catalog.noAccessBody}</p>
      </div>
    );
  }

  const activeUnits = units.filter((u) => u.is_active);
  const enabledCurrencies = currencies.filter((c) => c.enabled);

  function openNew() {
    const base = enabledCurrencies.find((c) => c.is_base)?.code ?? "";
    setForm({ ...emptyForm, unit_id: activeUnits.find((u) => u.code === "pcs")?.id ?? activeUnits[0]?.id ?? "", price_currency: base });
    setErrors({});
    setDrawer("new");
  }
  function openEdit(p: Product) { setForm(toForm(p)); setErrors({}); setDrawer(p); }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const local: Record<string, string> = {};
    if (!form.sku.trim()) local.sku = t.auth.required;
    if (!form.name.trim()) local.name = t.auth.required;
    if (!form.unit_id) local.unit_id = t.auth.required;
    setErrors(local);
    if (Object.keys(local).length) return;
    setFormBusy(true);
    try {
      const body = toBody(form);
      if (drawer === "new") await api(`/organizations/${orgId}/products`, { method: "POST", body });
      else if (drawer) await api(`/organizations/${orgId}/products/${drawer.id}`, { method: "PATCH", body });
      setDrawer(null);
      await load();
    } catch (err) {
      const fe = fieldErrorMap(t, err);
      setErrors(Object.keys(fe).length ? fe : { name: errorText(t, toApiError(err)) });
    } finally {
      setFormBusy(false);
    }
  }

  async function setArchived(p: Product, archived: boolean) {
    setActionError(null);
    setBusyId(p.id);
    try {
      await api(`/organizations/${orgId}/products/${p.id}/${archived ? "archive" : "restore"}`, { method: "POST" });
      await load();
    } catch (err) {
      setActionError(toApiError(err));
    } finally {
      setBusyId(null);
      setArchiving(null);
    }
  }

  const columns: Column<Product>[] = [
    { key: "sku", header: t.products.colSku, sortKey: "sku", render: (p) => <strong>{p.sku}</strong> },
    {
      key: "name", header: t.products.colName, sortKey: "name",
      render: (p) => (<>{p.name}{p.archived ? <> <span className="badge badge-muted">{t.products.archived}</span></> : null}{p.barcodes.length ? <div className="sub">{p.barcodes.join(", ")}</div> : null}</>),
    },
    { key: "kind", header: t.products.colKind, render: (p) => <span className={`badge ${p.kind === "goods" ? "badge-ok" : "badge-info"}`}>{p.kind === "goods" ? t.products.goods : t.products.service}</span> },
    { key: "unit", header: t.products.colUnit, render: (p) => pickName(p.unit, locale) },
    { key: "price", header: t.products.colPrice, render: (p) => (p.price && p.price_currency ? <span className="num">{formatMoney(p.price, p.price_currency, locale)}</span> : "—") },
  ];
  if (canManage) {
    columns.push({
      key: "actions", header: t.products.colActions,
      render: (p) => (
        <div className="row-actions">
          {!p.archived ? <Button variant="secondary" className="btn-sm" aria-label={`${t.products.edit}: ${p.sku}`} onClick={() => openEdit(p)}>{t.products.edit}</Button> : null}
          {p.archived ? (
            <Button variant="secondary" className="btn-sm" loading={busyId === p.id} aria-label={`${t.products.restore}: ${p.sku}`} onClick={() => void setArchived(p, false)}>{t.products.restore}</Button>
          ) : (
            <Button variant="secondary" className="btn-sm" disabled={busyId === p.id} aria-label={`${t.products.archive}: ${p.sku}`} onClick={() => setArchiving(p)}>{t.products.archive}</Button>
          )}
        </div>
      ),
    });
  }

  const filtered = !!(state.q || state.filters.kind !== "all" || state.filters.status !== "active");

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{t.products.title}</h1>
        {canManage ? <Button onClick={openNew}>{t.products.add}</Button> : null}
      </div>

      <div className="toolbar">
        <input type="search" value={searchText} onChange={(e) => setSearchText(e.target.value)}
          placeholder={t.products.searchPlaceholder} aria-label={t.list.search} maxLength={100} />
        <select aria-label={t.products.filterKind} value={state.filters.kind} onChange={(e) => update({ filters: { kind: e.target.value } })}>
          <option value="all">{t.products.kindAll}</option>
          <option value="goods">{t.products.kindGoods}</option>
          <option value="service">{t.products.kindService}</option>
        </select>
        <select aria-label={t.products.filterStatus} value={state.filters.status} onChange={(e) => update({ filters: { status: e.target.value } })}>
          <option value="active">{t.products.statusActive}</option>
          <option value="archived">{t.products.statusArchived}</option>
          <option value="all">{t.products.statusAll}</option>
        </select>
        {filtered ? (
          <Button variant="secondary" onClick={() => { setSearchText(""); update({ q: "", filters: { kind: "all", status: "active" } }); }}>{t.list.reset}</Button>
        ) : null}
      </div>

      {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}

      <DataTable
        caption={t.products.title}
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
        rowKey={(p) => p.id}
        empty={
          filtered ? (
            <p className="muted">{t.list.emptyFiltered}</p>
          ) : (
            <>
              <p className="muted">{t.products.empty}</p>
              {canManage ? <Button onClick={openNew}>{t.products.add}</Button> : null}
            </>
          )
        }
      />

      <Drawer open={drawer !== null} title={drawer === "new" ? t.products.newTitle : t.products.editTitle} onClose={() => setDrawer(null)}>
        <form onSubmit={save} noValidate>
          <div className="field">
            <label htmlFor="pr-kind">{t.products.fKind}</label>
            <select id="pr-kind" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as Form["kind"] })}>
              <option value="goods">{t.products.goods}</option>
              <option value="service">{t.products.service}</option>
            </select>
          </div>
          <Field id="pr-sku" label={t.products.fSku} value={form.sku} maxLength={64} onChange={(e) => setForm({ ...form, sku: e.target.value })} error={errors.sku} />
          <Field id="pr-name" label={t.products.fName} value={form.name} maxLength={200} onChange={(e) => setForm({ ...form, name: e.target.value })} error={errors.name} />
          <div className="field">
            <label htmlFor="pr-unit">{t.products.fUnit}</label>
            <select id="pr-unit" value={form.unit_id} onChange={(e) => setForm({ ...form, unit_id: e.target.value })} aria-invalid={errors.unit_id ? true : undefined}>
              {activeUnits.map((u) => <option key={u.id} value={u.id}>{pickName(u, locale)} ({u.code})</option>)}
              {drawer !== "new" && drawer && !activeUnits.some((u) => u.id === form.unit_id) ? <option value={form.unit_id}>{pickName(drawer.unit, locale)} ({drawer.unit.code})</option> : null}
            </select>
            {errors.unit_id ? <p className="field-error" role="alert">{errors.unit_id}</p> : null}
          </div>
          <div className="price-row">
            <Field id="pr-price" label={t.products.fPrice} inputMode="decimal" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} error={errors.price} hint={t.products.fPriceHint} />
            <div className="field">
              <label htmlFor="pr-cur">{t.products.fCurrency}</label>
              <select id="pr-cur" value={form.price_currency} onChange={(e) => setForm({ ...form, price_currency: e.target.value })}>
                <option value="">{t.products.noPrice}</option>
                {enabledCurrencies.map((c) => <option key={c.code} value={c.code}>{c.code}</option>)}
                {form.price_currency && !enabledCurrencies.some((c) => c.code === form.price_currency) ? <option value={form.price_currency}>{form.price_currency}</option> : null}
              </select>
              {errors.price_currency ? <p className="field-error" role="alert">{errors.price_currency}</p> : null}
            </div>
          </div>
          <Field id="pr-ikpu" label={t.products.fIkpu} value={form.ikpu} maxLength={50} onChange={(e) => setForm({ ...form, ikpu: e.target.value })} error={errors.ikpu} hint={t.products.fIkpuHint} />
          <TextArea id="pr-barcodes" label={t.products.fBarcodes} value={form.barcodes} onChange={(e) => setForm({ ...form, barcodes: e.target.value })} error={errors.barcodes} />
          <p className="hint" style={{ marginTop: -8, marginBottom: 12 }}>{t.products.fBarcodesHint}</p>
          <TextArea id="pr-desc" label={t.products.fDescription} value={form.description} maxLength={2000} onChange={(e) => setForm({ ...form, description: e.target.value })} error={errors.description} />
          <div className="drawer-actions">
            <Button type="submit" loading={formBusy}>{t.catalog.saveNames}</Button>
            <Button type="button" variant="secondary" onClick={() => setDrawer(null)}>{t.catalog.cancel}</Button>
          </div>
        </form>
      </Drawer>

      <ConfirmDialog
        open={archiving !== null}
        title={t.products.archiveTitle}
        body={t.products.archiveBody}
        confirmLabel={t.catalog.confirm}
        busy={busyId !== null}
        onCancel={() => setArchiving(null)}
        onConfirm={() => archiving && void setArchived(archiving, true)}
      />
    </div>
  );
}
