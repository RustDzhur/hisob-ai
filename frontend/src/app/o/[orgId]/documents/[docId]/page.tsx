"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { EntityPicker } from "@/components/EntityPicker";
import { ExtractionPanel, type Suggestion } from "@/components/ExtractionPanel";
import { StatusBadge } from "@/components/StatusBadge";
import { Banner, Button, ConfirmDialog, ErrorState, Field, Modal, Skeleton, TextArea } from "@/components/ui";
import { errorText, fieldErrorMap, fill } from "@/i18n/errors";
import { useFormat, useLocale, useT } from "@/i18n/LocaleProvider";
import { pickName } from "@/i18n/names";
import {
  api, toApiError, type ApiError as ApiErrorT, type Counterparty, type Currency, type DocIssue,
  type DocumentFull, type DocumentListItem, type MatchCandidate, type Page, type Product, type Unit,
} from "@/lib/api";
import { normalizeMoneyInput } from "@/lib/money";
import { useSession } from "@/lib/session";
import { suggestionState, valueForForm, type Kind } from "@/lib/suggestions";

type LineForm = {
  description: string; product_id: string; product_label: string; unit_id: string;
  quantity: string; unit_price: string; discount: string; tax_amount: string; amount: string;
};
type Form = {
  doc_type: string; number: string; doc_date: string; counterparty_id: string; counterparty_label: string;
  currency: string; total_amount: string; tax_total: string; notes: string; lines: LineForm[];
};

const DOC_TYPES = ["invoice", "esf", "waybill", "ttn", "act", "contract", "receipt", "statement", "payment_order", "return", "other"];
const emptyLine: LineForm = { description: "", product_id: "", product_label: "", unit_id: "", quantity: "", unit_price: "", discount: "", tax_amount: "", amount: "" };

const fromDoc = (d: DocumentFull): Form => ({
  doc_type: d.doc_type, number: d.number ?? "", doc_date: d.doc_date ?? "",
  counterparty_id: d.counterparty?.id ?? "", counterparty_label: d.counterparty?.name ?? "",
  currency: d.currency ?? "", total_amount: d.total_amount ?? "", tax_total: d.tax_total ?? "", notes: d.notes ?? "",
  lines: d.lines.map((l) => ({
    description: l.description, product_id: l.product_id ?? "", product_label: l.product_sku ?? "", unit_id: l.unit_id ?? "",
    quantity: l.quantity ?? "", unit_price: l.unit_price ?? "", discount: l.discount ?? "", tax_amount: l.tax_amount ?? "", amount: l.amount ?? "",
  })),
});

const num = (s: string) => normalizeMoneyInput(s) || null;
const toPatch = (f: Form, version: number) => ({
  expected_version: version, doc_type: f.doc_type, number: f.number || null, doc_date: f.doc_date || null,
  counterparty_id: f.counterparty_id || null, currency: f.currency || null,
  total_amount: num(f.total_amount), tax_total: num(f.tax_total), notes: f.notes || null,
  lines: f.lines.map((l) => ({
    description: l.description, product_id: l.product_id || null, unit_id: l.unit_id || null,
    quantity: num(l.quantity), unit_price: num(l.unit_price), discount: num(l.discount),
    tax_amount: num(l.tax_amount), amount: num(l.amount),
  })),
});

const HEADER_MAP: Record<string, { key: keyof Form; kind: Kind }> = {
  doc_type: { key: "doc_type", kind: "text" }, number: { key: "number", kind: "text" }, date: { key: "doc_date", kind: "date" },
  currency: { key: "currency", kind: "text" }, total_amount: { key: "total_amount", kind: "decimal" }, tax_total: { key: "tax_total", kind: "decimal" },
};
const LINE_MAP: Record<string, { key: keyof LineForm; kind: Kind }> = {
  description: { key: "description", kind: "text" }, quantity: { key: "quantity", kind: "decimal" }, unit_price: { key: "unit_price", kind: "decimal" },
  discount: { key: "discount", kind: "decimal" }, tax_amount: { key: "tax_amount", kind: "decimal" }, amount: { key: "amount", kind: "decimal" },
};

export default function DocumentPage() {
  const t = useT();
  const { locale } = useLocale();
  const { dateTime } = useFormat();
  const { orgId, docId } = useParams<{ orgId: string; docId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("documents.read");
  const canReview = !!org?.permissions.includes("documents.review");
  const canApprove = !!org?.permissions.includes("documents.approve");

  const [doc, setDoc] = useState<DocumentFull | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [error, setError] = useState<ApiErrorT | null>(null);
  const [actionError, setActionError] = useState<ApiErrorT | null>(null);
  const [fieldErrs, setFieldErrs] = useState<Record<string, string>>({});
  const [conflict, setConflict] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState(false);

  const [currencies, setCurrencies] = useState<Currency[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);

  const [dlg, setDlg] = useState<"approve" | "reject" | "duplicate" | "archive" | null>(null);
  const [ack, setAck] = useState<Record<string, boolean>>({});
  const [reason, setReason] = useState("");
  const [original, setOriginal] = useState<{ id: string; label: string } | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const d = await api<DocumentFull>(`/organizations/${orgId}/documents/${docId}`);
      setDoc(d);
      setForm(fromDoc(d));
      setConflict(false);
    } catch (e) {
      setError(toApiError(e));
    }
  }, [orgId, docId]);

  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);

  useEffect(() => {
    if (!canRead) return;
    api<Currency[]>(`/organizations/${orgId}/currencies`).then(setCurrencies).catch(() => setCurrencies([]));
    api<Unit[]>(`/organizations/${orgId}/units`).then(setUnits).catch(() => setUnits([]));
  }, [canRead, orgId]);

  const dirty = useMemo(() => !!doc && !!form && JSON.stringify(form) !== JSON.stringify(fromDoc(doc)), [doc, form]);
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
        <p className="muted">{t.documents.noAccessBody}</p>
      </div>
    );
  }
  if (error) {
    return error.status === 404 ? (
      <div className="card">
        <h1>{t.documents.notFound}</h1>
        <Link href={`/o/${orgId}/documents`}>{t.documents.back}</Link>
      </div>
    ) : (
      <ErrorState error={error} onRetry={load} />
    );
  }
  if (!doc || !form) return <Skeleton lines={8} />;

  const editable = doc.status === "NEEDS_REVIEW" && canReview;
  const fileUrl = `/api/v1/organizations/${orgId}/documents/${doc.id}/file`;
  const errors = doc.issues.filter((i) => i.severity === "error");
  const warnings = doc.issues.filter((i) => i.severity === "warning");
  const activeUnits = units.filter((u) => u.is_active);
  const enabledCurrencies = currencies.filter((c) => c.enabled);
  const issueMsg = (i: DocIssue) => fill(t.issueText[i.code] ?? i.code, i.params);
  const setF = (patch: Partial<Form>) => setForm({ ...form, ...patch });
  const setLine = (idx: number, patch: Partial<LineForm>) =>
    setForm({ ...form, lines: form.lines.map((l, i) => (i === idx ? { ...l, ...patch } : l)) });

  function handleError(e: unknown) {
    const err = toApiError(e);
    if (err.code === "document_conflict") setConflict(true);
    else setActionError(err);
  }

  async function save() {
    if (!doc || !form) return;
    setActionError(null);
    setFieldErrs({});
    setBusy("save");
    try {
      const d = await api<DocumentFull>(`/organizations/${orgId}/documents/${doc.id}`, { method: "PATCH", body: toPatch(form, doc.version) });
      setDoc(d);
      setForm(fromDoc(d));
      setSavedAt(true);
      setTimeout(() => setSavedAt(false), 2500);
    } catch (e) {
      const fe = fieldErrorMap(t, e);
      if (Object.keys(fe).length) setFieldErrs(fe);
      handleError(e);
    } finally {
      setBusy(null);
    }
  }

  async function act(name: string, body: Record<string, unknown> = {}) {
    if (!doc) return;
    setActionError(null);
    setBusy(name);
    try {
      const d = await api<DocumentFull>(`/organizations/${orgId}/documents/${doc.id}/${name}`, { method: "POST", body: { expected_version: doc.version, ...body } });
      setDoc(d);
      setForm(fromDoc(d));
      setDlg(null);
    } catch (e) {
      handleError(e);
      setDlg(null);
    } finally {
      setBusy(null);
    }
  }

  const searchCp = async (q: string) =>
    (await api<Page<Counterparty>>(`/organizations/${orgId}/counterparties?page_size=10&sort=name&q=${encodeURIComponent(q)}`)).items;
  const searchProduct = async (q: string) =>
    (await api<Page<Product>>(`/organizations/${orgId}/products?page_size=10&sort=name&q=${encodeURIComponent(q)}`)).items;
  const searchDocs = async (q: string) =>
    (await api<Page<DocumentListItem>>(`/organizations/${orgId}/documents?page_size=10&status=all&q=${encodeURIComponent(q)}`)).items.filter((d) => d.id !== doc.id);

  const title = `${t.documents.types[doc.doc_type] ?? doc.doc_type}${doc.number ? ` № ${doc.number}` : ""}`;
  const dupIds = (warnings.find((w) => w.code === "possible_duplicate")?.params?.document_ids as string[] | undefined) ?? [];
  const err = (f: string) => fieldErrs[f];

  const lineLabels: Record<string, string> = {
    description: t.documents.lDesc, quantity: t.documents.lQty, unit_price: t.documents.lPrice,
    discount: t.documents.lDiscount, tax_amount: t.documents.lTax, amount: t.documents.lAmount,
  };
  const headerLabels: Record<string, string> = {
    doc_type: t.documents.fType, number: t.documents.fNumber, date: t.documents.fDate, currency: t.documents.fCurrency,
    total_amount: t.documents.fTotal, tax_total: t.documents.fTax,
  };
  const suggestions: Suggestion[] = [];
  for (const f of doc.extraction.fields) {
    const m = /^lines\.(\d+)\.(\w+)$/.exec(f.path);
    const spec = m ? LINE_MAP[m[2]] : HEADER_MAP[f.path];
    if (!spec || f.value === null) continue;
    let current = "";
    let label = headerLabels[f.path] ?? f.path;
    if (m) {
      const line = form.lines[Number(m[1]) - 1];
      if (!line) continue;
      current = line[spec.key as keyof LineForm] ?? "";
      label = `${lineLabels[m[2]]} ${m[1]}`;
    } else {
      current = String(form[spec.key as keyof Form] ?? "");
    }
    const st = suggestionState(current, f, spec.kind);
    if (st !== "differs") continue;
    suggestions.push({ path: f.path, label, current, value: valueForForm(spec.kind, f.path, f.value), confidence: f.confidence, evidence: f.evidence });
  }

  function applySuggestions(list: Suggestion[]) {
    if (!form) return;
    const next: Form = { ...form, lines: form.lines.map((l) => ({ ...l })) };
    for (const s of list) {
      const m = /^lines\.(\d+)\.(\w+)$/.exec(s.path);
      if (m) {
        const line = next.lines[Number(m[1]) - 1];
        const spec = LINE_MAP[m[2]];
        if (line && spec) (line as Record<string, string>)[spec.key] = s.value;
      } else if (HEADER_MAP[s.path]) {
        (next as Record<string, unknown>)[HEADER_MAP[s.path].key] = s.value;
      }
    }
    setForm(next);
  }

  const onExtractionDoc = (d: DocumentFull) => {
    setDoc(d);
    if (!dirty) setForm(fromDoc(d));
  };

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <p style={{ margin: 0 }}><Link href={`/o/${orgId}/documents`}>← {t.documents.back}</Link></p>
          <h1 style={{ marginTop: 4 }}>{title} <StatusBadge status={doc.status} /></h1>
          <p className="muted" style={{ margin: 0 }}>
            {t.documents.createdBy}: {doc.created_by_name ?? "—"} · {dateTime(doc.created_at)} · {t.documents.version} {doc.version}
            {doc.approved_by_name ? <> · {t.documents.approvedBy}: {doc.approved_by_name}</> : null}
          </p>
        </div>
        <div className="actions-bar">
          {doc.status === "NEEDS_REVIEW" && canApprove ? (
            <Button disabled={dirty || errors.length > 0} onClick={() => { setAck({}); setDlg("approve"); }}
              title={dirty ? t.documents.approveSaveFirst : undefined}>{t.documents.approve}</Button>
          ) : null}
          {doc.status === "NEEDS_REVIEW" && canReview ? (
            <>
              <Button variant="secondary" onClick={() => { setReason(""); setDlg("reject"); }}>{t.documents.reject}</Button>
              <Button variant="secondary" onClick={() => { setOriginal(null); setDlg("duplicate"); }}>{t.documents.markDuplicate}</Button>
            </>
          ) : null}
          {(doc.status === "APPROVED" && canApprove) || ((doc.status === "REJECTED" || doc.status === "DUPLICATE") && canReview) ? (
            <Button variant="secondary" loading={busy === "reopen"} onClick={() => void act("reopen")}>{t.documents.reopen}</Button>
          ) : null}
          {["NEEDS_REVIEW", "REJECTED", "DUPLICATE"].includes(doc.status) && canReview || (doc.status === "APPROVED" && canApprove) ? (
            <Button variant="secondary" onClick={() => setDlg("archive")}>{t.documents.archive}</Button>
          ) : null}
          {doc.status === "ARCHIVED" && canReview ? (
            <Button variant="secondary" loading={busy === "restore"} onClick={() => void act("restore")}>{t.documents.restore}</Button>
          ) : null}
        </div>
      </div>

      {conflict ? (
        <Banner>
          {t.documents.conflict} <Button variant="secondary" className="btn-sm" onClick={() => void load()}>{t.documents.reload}</Button>
        </Banner>
      ) : null}
      {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}
      {doc.rejection_reason ? <Banner>{t.documents.rejectedReason}: {doc.rejection_reason}</Banner> : null}
      {doc.duplicate_of_id ? (
        <Banner kind="info">{t.documents.duplicateOf}: <Link href={`/o/${orgId}/documents/${doc.duplicate_of_id}`}>{t.documents.openOriginal}</Link></Banner>
      ) : null}

      <div className="doc-layout">
        <section className="card viewer" aria-label={t.documents.original}>
          <h2>{t.documents.original}</h2>
          {doc.file ? (
            <>
              {doc.file.inline && doc.file.content_type.startsWith("image/") ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={fileUrl} alt={doc.file.original_name} />
              ) : doc.file.inline ? (
                <object data={fileUrl} type="application/pdf" aria-label={doc.file.original_name}>
                  <a href={fileUrl}>{doc.file.original_name}</a>
                </object>
              ) : (
                <p className="muted">{t.documents.noPreview}</p>
              )}
              <p className="hint">{doc.file.original_name} · {Math.max(1, Math.round(doc.file.size_bytes / 1024))} KB</p>
              <a className="btn btn-secondary btn-sm" href={`${fileUrl}?download=true`}>{t.documents.download}</a>
            </>
          ) : null}
        </section>

        <div className="stack">
          <section className="card" aria-labelledby="issues-h">
            <h2 id="issues-h">{t.documents.issues}</h2>
            {doc.issues.length === 0 ? <p className="muted">{t.documents.noIssues}</p> : null}
            {errors.length ? <h3 className="issue-h">{t.documents.errorsTitle}</h3> : null}
            {errors.map((i, k) => <div key={`e${k}`} className="issue issue-error" role="alert">{issueMsg(i)}</div>)}
            {warnings.length ? <h3 className="issue-h">{t.documents.warningsTitle}</h3> : null}
            {warnings.map((i, k) => (
              <div key={`w${k}`} className="issue issue-warning">
                {issueMsg(i)}
                {i.code === "possible_duplicate" ? dupIds.map((id) => <> <Link key={id} href={`/o/${orgId}/documents/${id}`}>{t.documents.open}</Link></>) : null}
              </div>
            ))}
          </section>

          <ExtractionPanel orgId={orgId} docId={doc.id} extraction={doc.extraction} canRun={canReview} editable={editable}
            suggestions={suggestions} onApply={applySuggestions} onDoc={onExtractionDoc}
            onCounterparty={(c: MatchCandidate) => setF({ counterparty_id: c.id, counterparty_label: c.name })} />

          <section className="card" aria-labelledby="fields-h">
            <h2 id="fields-h">{t.documents.fields}</h2>
            {!editable ? <p className="hint">{t.documents.readOnly}</p> : null}
            <fieldset disabled={!editable} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
              <div className="doc-grid">
                <div className="field">
                  <label htmlFor="d-type">{t.documents.fType}</label>
                  <select id="d-type" value={form.doc_type} onChange={(e) => setF({ doc_type: e.target.value })}>
                    {DOC_TYPES.map((k) => <option key={k} value={k}>{t.documents.types[k]}</option>)}
                  </select>
                </div>
                <Field id="d-number" label={t.documents.fNumber} value={form.number} maxLength={100} onChange={(e) => setF({ number: e.target.value })} error={err("number")} />
                <Field id="d-date" type="date" label={t.documents.fDate} value={form.doc_date} onChange={(e) => setF({ doc_date: e.target.value })} error={err("doc_date")} />
                <EntityPicker<Counterparty>
                  label={t.documents.fCounterparty} disabled={!editable}
                  value={form.counterparty_id ? { id: form.counterparty_id, label: form.counterparty_label } : null}
                  search={searchCp} toItem={(c) => ({ id: c.id, label: c.name, hint: c.tin ?? undefined })}
                  onChange={(it) => setF({ counterparty_id: it?.id ?? "", counterparty_label: it?.label ?? "" })}
                  error={err("counterparty_id")}
                />
                <div className="field">
                  <label htmlFor="d-cur">{t.documents.fCurrency}</label>
                  <select id="d-cur" value={form.currency} onChange={(e) => setF({ currency: e.target.value })}>
                    <option value="">{t.documents.noCurrency}</option>
                    {enabledCurrencies.map((c) => <option key={c.code} value={c.code}>{c.code}</option>)}
                    {form.currency && !enabledCurrencies.some((c) => c.code === form.currency) ? <option value={form.currency}>{form.currency}</option> : null}
                  </select>
                  {err("currency") ? <p className="field-error" role="alert">{err("currency")}</p> : null}
                </div>
                <Field id="d-total" inputMode="decimal" label={t.documents.fTotal} value={form.total_amount} onChange={(e) => setF({ total_amount: e.target.value })} error={err("total_amount")} />
                <Field id="d-tax" inputMode="decimal" label={t.documents.fTax} value={form.tax_total} onChange={(e) => setF({ tax_total: e.target.value })} error={err("tax_total")} />
              </div>
              <TextArea id="d-notes" label={t.documents.fNotes} value={form.notes} maxLength={2000} onChange={(e) => setF({ notes: e.target.value })} />

              <h3 className="issue-h">{t.documents.lines}</h3>
              {err("lines") ? <p className="field-error" role="alert">{err("lines")}</p> : null}
              {form.lines.length === 0 ? <p className="muted">{t.documents.noLines}</p> : (
                <div className="table-wrap">
                  <table className="lines-table">
                    <thead>
                      <tr>
                        <th>#</th><th>{t.documents.lDesc}</th><th>{t.documents.lProduct}</th><th>{t.documents.lUnit}</th>
                        <th>{t.documents.lQty}</th><th>{t.documents.lPrice}</th><th>{t.documents.lDiscount}</th>
                        <th>{t.documents.lTax}</th><th>{t.documents.lAmount}</th><th />
                      </tr>
                    </thead>
                    <tbody>
                      {form.lines.map((l, i) => (
                        <tr key={i}>
                          <td>{i + 1}</td>
                          <td><input aria-label={`${t.documents.lDesc} ${i + 1}`} value={l.description} maxLength={500} onChange={(e) => setLine(i, { description: e.target.value })} /></td>
                          <td style={{ minWidth: 150 }}>
                            <EntityPicker<Product> hideLabel label={`${t.documents.lProduct} ${i + 1}`} disabled={!editable}
                              value={l.product_id ? { id: l.product_id, label: l.product_label } : null}
                              search={searchProduct} toItem={(p) => ({ id: p.id, label: p.sku, hint: p.name })}
                              onChange={(it) => setLine(i, { product_id: it?.id ?? "", product_label: it?.label ?? "" })} />
                          </td>
                          <td>
                            <select aria-label={`${t.documents.lUnit} ${i + 1}`} value={l.unit_id} onChange={(e) => setLine(i, { unit_id: e.target.value })}>
                              <option value="">—</option>
                              {activeUnits.map((u) => <option key={u.id} value={u.id}>{pickName(u, locale)}</option>)}
                              {l.unit_id && !activeUnits.some((u) => u.id === l.unit_id) ? <option value={l.unit_id}>{units.find((u) => u.id === l.unit_id)?.code ?? "?"}</option> : null}
                            </select>
                          </td>
                          {(["quantity", "unit_price", "discount", "tax_amount", "amount"] as const).map((k) => (
                            <td key={k}><input inputMode="decimal" aria-label={`${({ quantity: t.documents.lQty, unit_price: t.documents.lPrice, discount: t.documents.lDiscount, tax_amount: t.documents.lTax, amount: t.documents.lAmount })[k]} ${i + 1}`} value={l[k]} onChange={(e) => setLine(i, { [k]: e.target.value })} /></td>
                          ))}
                          <td><Button type="button" variant="secondary" className="btn-sm" aria-label={`${t.documents.removeLine} ${i + 1}`} onClick={() => setForm({ ...form, lines: form.lines.filter((_, j) => j !== i) })}>✕</Button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div style={{ marginTop: 8 }}>
                <Button type="button" variant="secondary" className="btn-sm" disabled={form.lines.length >= 500} onClick={() => setForm({ ...form, lines: [...form.lines, { ...emptyLine }] })}>{t.documents.addLine}</Button>
              </div>
            </fieldset>

            {editable ? (
              <div className="drawer-actions" style={{ alignItems: "center" }}>
                <Button onClick={() => void save()} loading={busy === "save"} disabled={!dirty}>{t.documents.save}</Button>
                {dirty ? <span className="hint">{t.documents.unsaved}</span> : null}
                {savedAt && !dirty ? <span className="hint" role="status">{t.documents.saved}</span> : null}
              </div>
            ) : null}
          </section>
        </div>
      </div>

      <Modal open={dlg === "approve"} title={t.documents.approveTitle} onClose={() => setDlg(null)}
        footer={<>
          <Button variant="secondary" onClick={() => setDlg(null)}>{t.documents.cancel}</Button>
          <Button loading={busy === "approve"} disabled={warnings.some((w) => !ack[w.code])}
            onClick={() => void act("approve", { acknowledged: Array.from(new Set(warnings.map((w) => w.code))) })}>{t.documents.approve}</Button>
        </>}>
        <p className="muted">{t.documents.approveBody}</p>
        {warnings.length === 0 ? <p>{t.documents.noIssues}</p> : null}
        {Array.from(new Set(warnings.map((w) => w.code))).map((code) => {
          const w = warnings.find((x) => x.code === code)!;
          return (
            <label key={code} className="ack-row">
              <input type="checkbox" checked={!!ack[code]} onChange={(e) => setAck({ ...ack, [code]: e.target.checked })} />
              <span><strong>{t.documents.ack}:</strong> {issueMsg(w)}</span>
            </label>
          );
        })}
      </Modal>

      <Modal open={dlg === "reject"} title={t.documents.rejectTitle} onClose={() => setDlg(null)}
        footer={<>
          <Button variant="secondary" onClick={() => setDlg(null)}>{t.documents.cancel}</Button>
          <Button loading={busy === "reject"} disabled={!reason.trim()} onClick={() => void act("reject", { reason })}>{t.documents.reject}</Button>
        </>}>
        <TextArea id="reject-reason" label={t.documents.rejectReason} value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />
      </Modal>

      <Modal open={dlg === "duplicate"} title={t.documents.dupTitle} onClose={() => setDlg(null)}
        footer={<>
          <Button variant="secondary" onClick={() => setDlg(null)}>{t.documents.cancel}</Button>
          <Button loading={busy === "mark-duplicate"} disabled={!original} onClick={() => original && void act("mark-duplicate", { original_id: original.id })}>{t.documents.markDuplicate}</Button>
        </>}>
        <p className="muted">{t.documents.dupBody}</p>
        <EntityPicker<DocumentListItem> label={t.documents.dupPick} value={original} onChange={(it) => setOriginal(it)}
          search={searchDocs} toItem={(d) => ({ id: d.id, label: `${t.documents.types[d.doc_type] ?? d.doc_type} ${d.number ?? ""}`.trim(), hint: d.original_name ?? undefined })} />
      </Modal>

      <ConfirmDialog open={dlg === "archive"} title={t.documents.archiveTitle} body={t.documents.archiveBody}
        confirmLabel={t.documents.confirm} busy={busy === "archive"} onCancel={() => setDlg(null)} onConfirm={() => void act("archive")} />
    </div>
  );
}
