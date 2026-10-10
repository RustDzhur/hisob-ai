"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { EntityPicker } from "@/components/EntityPicker";
import { StockTabs } from "@/components/StockTabs";
import { Banner, Button, ConfirmDialog, ErrorState, Skeleton, TextArea } from "@/components/ui";
import { errorText } from "@/i18n/errors";
import { useFormat, useLocale, useT } from "@/i18n/LocaleProvider";
import { pickName } from "@/i18n/names";
import { api, toApiError, type ApiError, type Page, type Product, type StockCount, type StockCountLine } from "@/lib/api";
import { formatQuantity, normalizeMoneyInput, quantityDiff } from "@/lib/money";
import { useSession } from "@/lib/session";

type LineForm = { product_id: string; sku: string; name: string; unit: StockCountLine["unit"]; expected: string; counted: string };
type Form = { notes: string; lines: LineForm[] };

const fromCount = (c: StockCount): Form => ({
  notes: c.notes ?? "",
  lines: c.lines.map((l) => ({ product_id: l.product_id, sku: l.sku, name: l.name, unit: l.unit, expected: l.expected_quantity, counted: l.counted_quantity ?? "" })),
});

export default function CountPage() {
  const t = useT();
  const { locale } = useLocale();
  const { dateTime } = useFormat();
  const router = useRouter();
  const { orgId, countId } = useParams<{ orgId: string; countId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("stock.read");
  const canManage = !!org?.permissions.includes("stock.manage");
  const canPost = !!org?.permissions.includes("stock.post");

  const [count, setCount] = useState<StockCount | null>(null);
  const [form, setForm] = useState<Form>({ notes: "", lines: [] });
  const [error, setError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [conflict, setConflict] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [dlg, setDlg] = useState<"post" | "delete" | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const c = await api<StockCount>(`/organizations/${orgId}/stock/counts/${countId}`);
      setCount(c);
      setForm(fromCount(c));
      setConflict(false);
    } catch (e) {
      setError(toApiError(e));
    }
  }, [orgId, countId]);

  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);

  const dirty = useMemo(() => !!count && JSON.stringify(form) !== JSON.stringify(fromCount(count)), [count, form]);
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

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
      <div className="card"><h1>{t.stock.notFound}</h1><Link href={`/o/${orgId}/stock/counts`}>{t.stock.countBack}</Link></div>
    ) : <ErrorState error={error} onRetry={load} />;
  }
  if (!count) return <Skeleton lines={6} />;

  const posted = count.status === "POSTED";
  const editable = !posted && canManage;
  const setLine = (i: number, patch: Partial<LineForm>) => setForm({ ...form, lines: form.lines.map((l, j) => (j === i ? { ...l, ...patch } : l)) });
  const bodyLines = () => form.lines.map((l) => ({ product_id: l.product_id, counted_quantity: normalizeMoneyInput(l.counted) || null }));
  const searchProducts = async (q: string) =>
    (await api<Page<Product>>(`/organizations/${orgId}/products?page_size=10&sort=name&q=${encodeURIComponent(q)}`)).items.filter((p) => p.kind === "goods" && !form.lines.some((l) => l.product_id === p.id));

  function fail(e: unknown) {
    const a = toApiError(e);
    if (a.code === "count_conflict") setConflict(true);
    else setActionError(a);
  }

  async function save() {
    if (!count) return;
    setActionError(null);
    setBusy("save");
    try {
      const c = await api<StockCount>(`/organizations/${orgId}/stock/counts/${count.id}`, {
        method: "PATCH", body: { expected_version: count.version, notes: form.notes || null, lines: bodyLines() },
      });
      setCount(c);
      setForm(fromCount(c));
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }

  async function post() {
    if (!count) return;
    setActionError(null);
    setBusy("post");
    try {
      const c = await api<StockCount>(`/organizations/${orgId}/stock/counts/${count.id}/post`, { method: "POST", body: { expected_version: count.version } });
      setCount(c);
      setForm(fromCount(c));
    } catch (e) {
      fail(e);
    } finally {
      setDlg(null);
      setBusy(null);
    }
  }

  async function remove() {
    if (!count) return;
    setBusy("delete");
    try {
      await api(`/organizations/${orgId}/stock/counts/${count.id}?expected_version=${count.version}`, { method: "DELETE" });
      router.replace(`/o/${orgId}/stock/counts`);
    } catch (e) {
      fail(e);
      setDlg(null);
      setBusy(null);
    }
  }

  const countedAny = form.lines.some((l) => l.counted.trim() !== "");
  const appliedByProduct = new Map(count.lines.map((l) => [l.product_id, l.applied_delta]));

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <p style={{ margin: 0 }}><Link href={`/o/${orgId}/stock/counts`}>← {t.stock.countBack}</Link></p>
          <h1 style={{ marginTop: 4 }}>
            {t.stock.countTitle} · {count.warehouse.name} <span className={`badge ${posted ? "badge-ok" : "badge-warn"}`}>{t.stock.countStatuses[count.status]}</span>
          </h1>
          <p className="muted" style={{ margin: 0 }}>
            {t.stock.createdBy}: {count.created_by_name ?? "—"} · {dateTime(count.created_at)} · {t.stock.version} {count.version}
            {count.posted_by_name ? <> · {t.stock.postedBy}: {count.posted_by_name}</> : null}
          </p>
        </div>
        <div className="actions-bar">
          {!posted && canPost ? <Button disabled={dirty || !countedAny} title={dirty ? t.stock.postSaveFirst : undefined} onClick={() => setDlg("post")}>{t.stock.post}</Button> : null}
          {!posted && canManage ? <Button variant="secondary" onClick={() => setDlg("delete")}>{t.stock.countDelete}</Button> : null}
        </div>
      </div>
      <StockTabs />

      {conflict ? <Banner>{t.stock.countConflict} <Button variant="secondary" className="btn-sm" onClick={() => void load()}>{t.stock.reload}</Button></Banner> : null}
      {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}
      {posted ? (
        <Banner kind="info">
          {t.stock.countPostedInfo}{" "}
          {count.operation_id ? <Link href={`/o/${orgId}/stock/operations/${count.operation_id}`}>{t.stock.countOperation}</Link> : t.stock.countNoDiscrepancy}
        </Banner>
      ) : null}

      <section className="card" aria-labelledby="cnt-h">
        <h2 id="cnt-h">{t.stock.lines}</h2>
        <p className="hint">{t.stock.countNote}</p>
        <fieldset disabled={!editable} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <TextArea id="cnt-notes" label={t.stock.fNotes} value={form.notes} maxLength={2000} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          {form.lines.length === 0 ? <p className="muted">{t.stock.noLines}</p> : (
            <div className="table-wrap">
              <table className="lines-table">
                <thead><tr><th>{t.stock.colSku}</th><th>{t.stock.colProduct}</th><th>{t.stock.countExpected}</th><th>{t.stock.countCounted}</th><th>{t.stock.countDiff}</th><th>{t.stock.colUnit}</th></tr></thead>
                <tbody>
                  {form.lines.map((l, i) => {
                    const d = posted ? (appliedByProduct.get(l.product_id) ?? null) : quantityDiff(normalizeMoneyInput(l.counted), l.expected);
                    const unit = pickName(l.unit, locale) || l.unit.code;
                    return (
                      <tr key={l.product_id}>
                        <td>{l.sku}</td>
                        <td>{l.name}</td>
                        <td className="num">{formatQuantity(l.expected, locale)}</td>
                        <td>
                          <input inputMode="decimal" aria-label={`${t.stock.countCounted}: ${l.sku}`} value={l.counted} placeholder={t.stock.countNotCounted} onChange={(e) => setLine(i, { counted: e.target.value })} />
                        </td>
                        <td className={`num ${d && d.startsWith("-") ? "qty-neg" : d && d !== "0" ? "qty-pos" : ""}`}>{d === null ? "—" : `${d !== "0" && !d.startsWith("-") ? "+" : ""}${formatQuantity(d, locale)}`}</td>
                        <td>{unit}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {editable ? (
            <div style={{ marginTop: 8, maxWidth: 360 }}>
              <EntityPicker<Product> label={t.stock.countAddProduct} value={null} search={searchProducts}
                toItem={(p) => ({ id: p.id, label: p.sku, hint: p.name })}
                onChange={(it, raw) => {
                  if (!it || !raw) return;
                  setForm({ ...form, lines: [...form.lines, { product_id: it.id, sku: raw.sku, name: raw.name, unit: raw.unit, expected: "0", counted: "" }] });
                }} />
            </div>
          ) : null}
        </fieldset>
        {editable ? (
          <div className="drawer-actions" style={{ alignItems: "center" }}>
            <Button onClick={() => void save()} loading={busy === "save"} disabled={!dirty}>{t.stock.save}</Button>
            {dirty ? <span className="hint">{t.stock.unsaved}</span> : null}
            {saved && !dirty ? <span className="hint" role="status">{t.stock.saved}</span> : null}
          </div>
        ) : null}
      </section>

      <ConfirmDialog open={dlg === "post"} title={t.stock.countPostTitle} body={t.stock.countPostBody} confirmLabel={t.stock.post} busy={busy === "post"} onCancel={() => setDlg(null)} onConfirm={() => void post()} />
      <ConfirmDialog open={dlg === "delete"} title={t.stock.countDeleteTitle} body={t.stock.countDeleteBody} confirmLabel={t.stock.countDelete} busy={busy === "delete"} onCancel={() => setDlg(null)} onConfirm={() => void remove()} />
    </div>
  );
}
