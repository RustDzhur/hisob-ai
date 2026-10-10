"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { EntityPicker } from "@/components/EntityPicker";
import { StockTabs } from "@/components/StockTabs";
import { Banner, Button, ConfirmDialog, ErrorState, Field, Skeleton, TextArea } from "@/components/ui";
import { errorText, fieldErrorMap, fill } from "@/i18n/errors";
import { useFormat, useLocale, useT } from "@/i18n/LocaleProvider";
import { pickName } from "@/i18n/names";
import {
  api, toApiError, type ApiError as ApiErrorT, type Page, type Product, type StockOperation, type Warehouse,
} from "@/lib/api";
import { formatQuantity, normalizeMoneyInput } from "@/lib/money";
import { useSession } from "@/lib/session";

type LineForm = { product_id: string; label: string; quantity: string; unit?: string };
type Form = { kind: "receipt" | "issue" | "transfer"; op_date: string; warehouse_id: string; to_warehouse_id: string; reference: string; notes: string; lines: LineForm[] };

const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const blank = (): Form => ({ kind: "receipt", op_date: today(), warehouse_id: "", to_warehouse_id: "", reference: "", notes: "", lines: [{ product_id: "", label: "", quantity: "" }] });
const fromOp = (o: StockOperation): Form => ({
  kind: o.kind, op_date: o.op_date, warehouse_id: o.warehouse.id, to_warehouse_id: o.to_warehouse?.id ?? "",
  reference: o.reference ?? "", notes: o.notes ?? "",
  lines: o.lines.map((l) => ({ product_id: l.product_id, label: l.sku, quantity: l.quantity })),
});
const linesBody = (f: Form) => f.lines.map((l) => ({ product_id: l.product_id, quantity: normalizeMoneyInput(l.quantity) }));

export default function OperationPage() {
  const t = useT();
  const { locale } = useLocale();
  const { dateTime } = useFormat();
  const router = useRouter();
  const { orgId, opId } = useParams<{ orgId: string; opId: string }>();
  const isNew = opId === "new";
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("stock.read");
  const canManage = !!org?.permissions.includes("stock.manage");
  const canPost = !!org?.permissions.includes("stock.post");

  const [op, setOp] = useState<StockOperation | null>(null);
  const [form, setForm] = useState<Form>(blank);
  const [error, setError] = useState<ApiErrorT | null>(null);
  const [actionError, setActionError] = useState<ApiErrorT | null>(null);
  const [fieldErrs, setFieldErrs] = useState<Record<string, string>>({});
  const [conflict, setConflict] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [dlg, setDlg] = useState<"post" | "delete" | null>(null);
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);

  const load = useCallback(async () => {
    if (isNew) return;
    setError(null);
    try {
      const o = await api<StockOperation>(`/organizations/${orgId}/stock/operations/${opId}`);
      setOp(o);
      setForm(fromOp(o));
      setConflict(false);
    } catch (e) {
      setError(toApiError(e));
    }
  }, [orgId, opId, isNew]);

  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);
  useEffect(() => {
    if (canRead) api<Page<Warehouse>>(`/organizations/${orgId}/warehouses?page_size=100&status=active`).then((p) => setWarehouses(p.items)).catch(() => setWarehouses([]));
  }, [canRead, orgId]);

  const dirty = useMemo(() => (isNew ? true : !!op && JSON.stringify(form) !== JSON.stringify(fromOp(op))), [isNew, op, form]);
  useEffect(() => {
    if (!dirty || isNew) return;
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty, isNew]);

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) {
    return (
      <div className="card">
        <h1>{t.catalog.noAccessTitle}</h1>
        <p className="muted">{t.stock.noAccessBody}</p>
      </div>
    );
  }
  if (error) {
    return error.status === 404 ? (
      <div className="card">
        <h1>{t.stock.notFound}</h1>
        <Link href={`/o/${orgId}/stock/operations`}>{t.stock.back}</Link>
      </div>
    ) : (
      <ErrorState error={error} onRetry={load} />
    );
  }
  if (!isNew && !op) return <Skeleton lines={6} />;

  const posted = op?.status === "POSTED";
  const editable = (isNew || op?.status === "DRAFT") && canManage;
  const setF = (patch: Partial<Form>) => setForm({ ...form, ...patch });
  const setLine = (i: number, patch: Partial<LineForm>) => setForm({ ...form, lines: form.lines.map((l, j) => (j === i ? { ...l, ...patch } : l)) });
  const searchProducts = async (q: string) =>
    (await api<Page<Product>>(`/organizations/${orgId}/products?page_size=10&sort=name&q=${encodeURIComponent(q)}`)).items.filter((p) => p.kind === "goods");
  const err = (f: string) => fieldErrs[f];
  const incomplete = form.lines.some((l) => !l.product_id || !l.quantity.trim()) || !form.warehouse_id || (form.kind === "transfer" && !form.to_warehouse_id);

  function fail(e: unknown) {
    const a = toApiError(e);
    if (a.code === "operation_conflict") setConflict(true);
    else setActionError(a);
    const fe = fieldErrorMap(t, e);
    if (Object.keys(fe).length) setFieldErrs(fe);
  }

  async function submit() {
    setActionError(null);
    setFieldErrs({});
    setBusy("save");
    try {
      if (isNew) {
        const body = {
          kind: form.kind, op_date: form.op_date, warehouse_id: form.warehouse_id,
          to_warehouse_id: form.kind === "transfer" ? form.to_warehouse_id : null,
          reference: form.reference || null, notes: form.notes || null, lines: linesBody(form),
        };
        const created = await api<StockOperation>(`/organizations/${orgId}/stock/operations`, { method: "POST", body });
        router.replace(`/o/${orgId}/stock/operations/${created.id}`);
        return;
      }
      if (!op) return;
      const body = {
        expected_version: op.version, op_date: form.op_date, warehouse_id: form.warehouse_id,
        to_warehouse_id: op.kind === "transfer" ? form.to_warehouse_id : null,
        reference: form.reference || null, notes: form.notes || null, lines: linesBody(form),
      };
      const updated = await api<StockOperation>(`/organizations/${orgId}/stock/operations/${op.id}`, { method: "PATCH", body });
      setOp(updated);
      setForm(fromOp(updated));
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }

  async function post() {
    if (!op) return;
    setActionError(null);
    setBusy("post");
    try {
      const updated = await api<StockOperation>(`/organizations/${orgId}/stock/operations/${op.id}/post`, { method: "POST", body: { expected_version: op.version } });
      setOp(updated);
      setForm(fromOp(updated));
    } catch (e) {
      fail(e);
    } finally {
      setDlg(null);
      setBusy(null);
    }
  }

  async function remove() {
    if (!op) return;
    setBusy("delete");
    try {
      await api(`/organizations/${orgId}/stock/operations/${op.id}?expected_version=${op.version}`, { method: "DELETE" });
      router.replace(`/o/${orgId}/stock/operations`);
    } catch (e) {
      fail(e);
      setDlg(null);
      setBusy(null);
    }
  }

  const shortages = actionError?.code === "insufficient_stock" ? actionError.details : [];
  const title = isNew ? t.stock.newTitle : `${t.stock.kinds[op!.kind] ?? op!.kind}${op!.reference ? ` · ${op!.reference}` : ""}`;
  const warehouseOptions = (cur: string) => (
    <>
      <option value="">—</option>
      {warehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
      {cur && !warehouses.some((w) => w.id === cur) ? <option value={cur}>{op?.warehouse.id === cur ? op.warehouse.name : op?.to_warehouse?.name ?? cur}</option> : null}
    </>
  );

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <p style={{ margin: 0 }}><Link href={`/o/${orgId}/stock/operations`}>← {t.stock.back}</Link></p>
          <h1 style={{ marginTop: 4 }}>
            {title} {op ? <span className={`badge ${posted ? "badge-ok" : "badge-warn"}`}>{t.stock.statuses[op.status]}</span> : null}
          </h1>
          {op ? (
            <p className="muted" style={{ margin: 0 }}>
              {t.stock.createdBy}: {op.created_by_name ?? "—"} · {dateTime(op.created_at)} · {t.stock.version} {op.version}
              {op.posted_by_name ? <> · {t.stock.postedBy}: {op.posted_by_name} · {op.posted_at ? dateTime(op.posted_at) : ""}</> : null}
            </p>
          ) : null}
        </div>
        <div className="actions-bar">
          {op && !posted && canPost ? (
            <Button disabled={dirty || incomplete} title={dirty ? t.stock.postSaveFirst : undefined} onClick={() => setDlg("post")}>{t.stock.post}</Button>
          ) : null}
          {op && !posted && canManage ? <Button variant="secondary" onClick={() => setDlg("delete")}>{t.stock.delete}</Button> : null}
        </div>
      </div>
      <StockTabs />

      {conflict ? <Banner>{t.stock.conflict} <Button variant="secondary" className="btn-sm" onClick={() => void load()}>{t.stock.reload}</Button></Banner> : null}
      {actionError && shortages.length === 0 ? <Banner>{errorText(t, actionError)}</Banner> : null}
      {shortages.length ? (
        <Banner>
          {errorText(t, actionError!)}
          <ul>
            {shortages.map((d, i) => (
              <li key={i}>{fill(t.stock.shortage, { sku: d.params?.sku, required: formatQuantity(String(d.params?.required ?? ""), locale), available: formatQuantity(String(d.params?.available ?? ""), locale) })}</li>
            ))}
          </ul>
        </Banner>
      ) : null}
      {posted ? <Banner kind="info">{t.stock.readOnly}</Banner> : null}

      <section className="card" aria-labelledby="op-h">
        <h2 id="op-h">{t.stock.opTitle}</h2>
        <p className="hint">{t.stock.note}</p>
        <fieldset disabled={!editable} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <div className="doc-grid">
            <div className="field">
              <label htmlFor="o-kind">{t.stock.fKind}</label>
              <select id="o-kind" value={form.kind} disabled={!isNew} onChange={(e) => setF({ kind: e.target.value as Form["kind"], to_warehouse_id: "" })}>
                {Object.entries(t.stock.kinds).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <Field id="o-date" type="date" label={t.stock.fDate} value={form.op_date} onChange={(e) => setF({ op_date: e.target.value })} error={err("op_date")} />
            <div className="field">
              <label htmlFor="o-wh">{form.kind === "transfer" ? t.stock.fWarehouseFrom : t.stock.fWarehouse}</label>
              <select id="o-wh" value={form.warehouse_id} onChange={(e) => setF({ warehouse_id: e.target.value })}>{warehouseOptions(form.warehouse_id)}</select>
              {err("warehouse_id") ? <p className="field-error" role="alert">{err("warehouse_id")}</p> : null}
            </div>
            {form.kind === "transfer" ? (
              <div className="field">
                <label htmlFor="o-to">{t.stock.fWarehouseTo}</label>
                <select id="o-to" value={form.to_warehouse_id} onChange={(e) => setF({ to_warehouse_id: e.target.value })}>{warehouseOptions(form.to_warehouse_id)}</select>
                {err("to_warehouse_id") ? <p className="field-error" role="alert">{err("to_warehouse_id")}</p> : null}
              </div>
            ) : null}
            <Field id="o-ref" label={t.stock.fReference} value={form.reference} maxLength={100} onChange={(e) => setF({ reference: e.target.value })} />
          </div>
          <TextArea id="o-notes" label={t.stock.fNotes} value={form.notes} maxLength={2000} onChange={(e) => setF({ notes: e.target.value })} />

          <h3 className="issue-h">{t.stock.lines}</h3>
          {err("lines") ? <p className="field-error" role="alert">{err("lines")}</p> : null}
          {form.lines.length === 0 ? <p className="muted">{t.stock.noLines}</p> : (
            <div className="table-wrap">
              <table className="lines-table">
                <thead><tr><th>#</th><th>{t.stock.lProduct}</th><th>{t.stock.lQty}</th><th>{t.stock.colUnit}</th><th /></tr></thead>
                <tbody>
                  {form.lines.map((l, i) => {
                    const unit = op?.lines.find((x) => x.product_id === l.product_id)?.unit;
                    return (
                      <tr key={i}>
                        <td>{i + 1}</td>
                        <td style={{ minWidth: 200 }}>
                          <EntityPicker<Product> hideLabel label={`${t.stock.lProduct} ${i + 1}`} disabled={!editable}
                            value={l.product_id ? { id: l.product_id, label: l.label } : null}
                            search={searchProducts} toItem={(p) => ({ id: p.id, label: p.sku, hint: p.name })}
                            onChange={(it, raw) => setLine(i, { product_id: it?.id ?? "", label: it?.label ?? "", unit: raw ? pickName(raw.unit, locale) || raw.unit.code : undefined })}
                            error={err(`lines.${i + 1}.product_id`)} />
                        </td>
                        <td><input inputMode="decimal" aria-label={`${t.stock.lQty} ${i + 1}`} value={l.quantity} onChange={(e) => setLine(i, { quantity: e.target.value })} /></td>
                        <td>{l.unit ?? (unit ? pickName(unit, locale) || unit.code : "")}</td>
                        <td><Button type="button" variant="secondary" className="btn-sm" aria-label={`${t.stock.removeLine} ${i + 1}`} disabled={form.lines.length <= 1} onClick={() => setForm({ ...form, lines: form.lines.filter((_, j) => j !== i) })}>✕</Button></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div style={{ marginTop: 8 }}>
            <Button type="button" variant="secondary" className="btn-sm" disabled={form.lines.length >= 500} onClick={() => setForm({ ...form, lines: [...form.lines, { product_id: "", label: "", quantity: "" }] })}>{t.stock.addLine}</Button>
          </div>
        </fieldset>

        {editable ? (
          <div className="drawer-actions" style={{ alignItems: "center" }}>
            <Button onClick={() => void submit()} loading={busy === "save"} disabled={(!isNew && !dirty) || incomplete}>{isNew ? t.stock.create : t.stock.save}</Button>
            {!isNew && dirty ? <span className="hint">{t.stock.unsaved}</span> : null}
            {saved && !dirty ? <span className="hint" role="status">{t.stock.saved}</span> : null}
          </div>
        ) : null}
      </section>

      <ConfirmDialog open={dlg === "post"} title={t.stock.postTitle} body={t.stock.postBody} confirmLabel={t.stock.post} busy={busy === "post"} onCancel={() => setDlg(null)} onConfirm={() => void post()} />
      <ConfirmDialog open={dlg === "delete"} title={t.stock.deleteTitle} body={t.stock.deleteBody} confirmLabel={t.stock.delete} busy={busy === "delete"} onCancel={() => setDlg(null)} onConfirm={() => void remove()} />
    </div>
  );
}
