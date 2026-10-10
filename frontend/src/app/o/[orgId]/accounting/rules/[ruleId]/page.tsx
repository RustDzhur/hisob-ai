"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { AccountingTabs } from "@/components/AccountingTabs";
import { EntityPicker } from "@/components/EntityPicker";
import { Banner, Button, ConfirmDialog, ErrorState, Field, Modal, Skeleton, TextArea } from "@/components/ui";
import { errorText, fieldErrorMap } from "@/i18n/errors";
import { useFormat, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type Account, type ApiError, type Page, type PostingRule } from "@/lib/api";
import { useSession } from "@/lib/session";

type LineForm = { account_id: string; label: string; side: "debit" | "credit"; amount_source: "total_amount" | "tax_total" | "total_minus_tax"; memo: string };
type Form = { rule_key: string; name: string; description: string; doc_type: string; effective_from: string; effective_to: string; lines: LineForm[] };

const DOC_TYPES = ["invoice", "esf", "waybill", "ttn", "act", "contract", "receipt", "statement", "payment_order", "return", "other"];
const COEFF = { total_amount: [1, 0], tax_total: [0, 1], total_minus_tax: [1, -1] } as const;
const blankLine = (side: "debit" | "credit"): LineForm => ({ account_id: "", label: "", side, amount_source: "total_amount", memo: "" });
const blank = (): Form => ({ rule_key: "", name: "", description: "", doc_type: "invoice", effective_from: "", effective_to: "", lines: [blankLine("debit"), blankLine("credit")] });
const fromRule = (r: PostingRule): Form => ({
  rule_key: r.rule_key, name: r.name, description: r.description ?? "", doc_type: r.doc_type, effective_from: r.effective_from ?? "", effective_to: r.effective_to ?? "",
  lines: r.lines.map((l) => ({ account_id: l.account_id, label: l.account_code, side: l.side, amount_source: l.amount_source, memo: l.memo ?? "" })),
});

/** Правило сбалансировано символьно: дебет и кредит совпадают при любых «итого» и «налог». */
function isBalanced(lines: LineForm[]): boolean {
  const d = [0, 0];
  const c = [0, 0];
  for (const l of lines) {
    const target = l.side === "debit" ? d : c;
    target[0] += COEFF[l.amount_source][0];
    target[1] += COEFF[l.amount_source][1];
  }
  return d[0] === c[0] && d[1] === c[1] && (d[0] !== 0 || d[1] !== 0);
}

export default function RulePage() {
  const t = useT();
  const { dateTime } = useFormat();
  const router = useRouter();
  const { orgId, ruleId } = useParams<{ orgId: string; ruleId: string }>();
  const isNew = ruleId === "new";
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("accounting.read");
  const canManage = !!org?.permissions.includes("accounting.manage");

  const [rule, setRule] = useState<PostingRule | null>(null);
  const [form, setForm] = useState<Form>(blank);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [fieldErrs, setFieldErrs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [dlg, setDlg] = useState<"approve" | "retire" | "delete" | null>(null);
  const [basis, setBasis] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!canRead || isNew) return;
    api<PostingRule>(`/organizations/${orgId}/accounting/rules/${ruleId}`).then((r) => { setRule(r); setForm(fromRule(r)); }).catch((e) => setError(toApiError(e)));
  }, [canRead, isNew, orgId, ruleId]);

  const dirty = useMemo(() => isNew || (!!rule && JSON.stringify(form) !== JSON.stringify(fromRule(rule))), [isNew, rule, form]);

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) return <div className="card"><h1>{t.catalog.noAccessTitle}</h1><p className="muted">{t.accounting.noAccessBody}</p></div>;
  if (error) return error.status === 404 ? <div className="card"><h1>{t.accounting.notFound}</h1><Link href={`/o/${orgId}/accounting/rules`}>{t.accounting.rules}</Link></div> : <ErrorState error={error} />;
  if (!isNew && !rule) return <Skeleton lines={6} />;

  const editable = (isNew || rule?.status === "DRAFT") && canManage;
  const setLine = (i: number, patch: Partial<LineForm>) => setForm({ ...form, lines: form.lines.map((l, j) => (j === i ? { ...l, ...patch } : l)) });
  const searchAccounts = async (q: string) => (await api<Page<Account>>(`/organizations/${orgId}/accounting/accounts?page_size=10&q=${encodeURIComponent(q)}`)).items.filter((a) => a.allow_posting);
  const balanced = isBalanced(form.lines);
  const complete = form.name.trim() !== "" && (!isNew || form.rule_key.trim() !== "") && form.lines.every((l) => l.account_id);
  const err = (f: string) => fieldErrs[f];
  const linesBody = () => form.lines.map((l) => ({ account_id: l.account_id, side: l.side, amount_source: l.amount_source, memo: l.memo || null }));

  async function run(name: string, fn: () => Promise<void>) {
    setActionError(null);
    setFieldErrs({});
    setBusy(name);
    try {
      await fn();
    } catch (e) {
      const fe = fieldErrorMap(t, e);
      if (Object.keys(fe).length) setFieldErrs(fe);
      setActionError(toApiError(e));
    } finally {
      setBusy(null);
      setDlg(null);
    }
  }

  const save = () => run("save", async () => {
    if (isNew) {
      const r = await api<PostingRule>(`/organizations/${orgId}/accounting/rules`, {
        method: "POST",
        body: { rule_key: form.rule_key, name: form.name, description: form.description || null, doc_type: form.doc_type, effective_from: form.effective_from || null, effective_to: form.effective_to || null, lines: linesBody() },
      });
      router.replace(`/o/${orgId}/accounting/rules/${r.id}`);
      return;
    }
    const r = await api<PostingRule>(`/organizations/${orgId}/accounting/rules/${rule!.id}`, {
      method: "PATCH",
      body: { name: form.name, description: form.description || null, doc_type: form.doc_type, effective_from: form.effective_from || null, effective_to: form.effective_to || null, lines: linesBody() },
    });
    setRule(r);
    setForm(fromRule(r));
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  });
  const approve = () => run("approve", async () => {
    const r = await api<PostingRule>(`/organizations/${orgId}/accounting/rules/${rule!.id}/approve`, { method: "POST", body: { legal_basis: basis } });
    setRule(r);
    setForm(fromRule(r));
  });
  const retire = () => run("retire", async () => {
    const r = await api<PostingRule>(`/organizations/${orgId}/accounting/rules/${rule!.id}/retire`, { method: "POST" });
    setRule(r);
  });
  const remove = () => run("delete", async () => {
    await api(`/organizations/${orgId}/accounting/rules/${rule!.id}`, { method: "DELETE" });
    router.replace(`/o/${orgId}/accounting/rules`);
  });
  const newVersion = () => run("version", async () => {
    const r = await api<PostingRule>(`/organizations/${orgId}/accounting/rules/${rule!.id}/new-version`, { method: "POST" });
    router.push(`/o/${orgId}/accounting/rules/${r.id}`);
  });

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <p style={{ margin: 0 }}><Link href={`/o/${orgId}/accounting/rules`}>← {t.accounting.rulesTitle}</Link></p>
          <h1 style={{ marginTop: 4 }}>
            {isNew ? t.accounting.newRule : rule!.name}{" "}
            {rule ? <span className={`badge ${rule.status === "APPROVED" ? "badge-ok" : rule.status === "DRAFT" ? "badge-warn" : "badge-muted"}`}>{t.accounting.ruleStatuses[rule.status]}</span> : null}
          </h1>
          {rule ? <p className="muted" style={{ margin: 0 }}>{rule.rule_key} · {t.accounting.ruleVersion} {rule.version}{rule.approved_by_name ? ` · ${t.accounting.ruleApprovedBy}: ${rule.approved_by_name} · ${rule.approved_at ? dateTime(rule.approved_at) : ""}` : ""}</p> : null}
        </div>
        <div className="actions-bar">
          {rule?.status === "DRAFT" && canManage ? <Button disabled={dirty || !balanced} title={dirty ? t.accounting.save : undefined} onClick={() => { setBasis(""); setDlg("approve"); }}>{t.accounting.ruleApprove}</Button> : null}
          {rule?.status === "DRAFT" && canManage ? <Button variant="secondary" onClick={() => setDlg("delete")}>{t.accounting.ruleDelete}</Button> : null}
          {rule && rule.status !== "DRAFT" && canManage ? <Button variant="secondary" loading={busy === "version"} onClick={() => void newVersion()}>{t.accounting.ruleNewVersion}</Button> : null}
          {rule?.status === "APPROVED" && canManage ? <Button variant="secondary" onClick={() => setDlg("retire")}>{t.accounting.ruleRetire}</Button> : null}
        </div>
      </div>
      <AccountingTabs />
      {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}
      {rule && rule.status !== "DRAFT" ? <Banner kind="info">{t.accounting.ruleReadOnly}{rule.legal_basis ? ` ${t.accounting.ruleBasis}: ${rule.legal_basis}` : ""}</Banner> : null}
      <section className="card" aria-labelledby="rule-h">
        <h2 id="rule-h">{t.accounting.rulesTitle}</h2>
        <p className="hint">{t.accounting.rulesNote}</p>
        <fieldset disabled={!editable} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <div className="doc-grid">
            <Field id="r-key" label={t.accounting.ruleKey} value={form.rule_key} maxLength={64} disabled={!isNew} onChange={(e) => setForm({ ...form, rule_key: e.target.value })} error={err("rule_key")} />
            <Field id="r-name" label={t.accounting.ruleName} value={form.name} maxLength={300} onChange={(e) => setForm({ ...form, name: e.target.value })} error={err("name")} />
            <div className="field">
              <label htmlFor="r-type">{t.accounting.ruleDocType}</label>
              <select id="r-type" value={form.doc_type} onChange={(e) => setForm({ ...form, doc_type: e.target.value })}>
                {DOC_TYPES.map((k) => <option key={k} value={k}>{t.documents.types[k]}</option>)}
              </select>
            </div>
            <Field id="r-from" type="date" label={t.accounting.ruleFrom} value={form.effective_from} onChange={(e) => setForm({ ...form, effective_from: e.target.value })} />
            <Field id="r-to" type="date" label={t.accounting.ruleTo} value={form.effective_to} onChange={(e) => setForm({ ...form, effective_to: e.target.value })} error={err("effective_to")} />
          </div>
          <TextArea id="r-desc" label={t.accounting.ruleDescription} value={form.description} maxLength={2000} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <h3 className="issue-h">{t.accounting.lines}</h3>
          {err("lines") ? <p className="field-error" role="alert">{err("lines")}</p> : null}
          <div className="table-wrap">
            <table className="lines-table">
              <thead><tr><th>#</th><th>{t.accounting.lAccount}</th><th>{t.accounting.ruleSide}</th><th>{t.accounting.ruleSource}</th><th>{t.accounting.lMemo}</th><th /></tr></thead>
              <tbody>
                {form.lines.map((l, i) => (
                  <tr key={i}>
                    <td>{i + 1}</td>
                    <td style={{ minWidth: 200 }}>
                      <EntityPicker<Account> hideLabel label={`${t.accounting.lAccount} ${i + 1}`} disabled={!editable} value={l.account_id ? { id: l.account_id, label: l.label } : null}
                        search={searchAccounts} toItem={(a) => ({ id: a.id, label: a.code, hint: a.name })} onChange={(it) => setLine(i, { account_id: it?.id ?? "", label: it?.label ?? "" })} error={err(`lines.${i + 1}.account_id`)} />
                    </td>
                    <td>
                      <select aria-label={`${t.accounting.ruleSide} ${i + 1}`} value={l.side} onChange={(e) => setLine(i, { side: e.target.value as LineForm["side"] })}>
                        {Object.entries(t.accounting.ruleSides).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                      </select>
                    </td>
                    <td>
                      <select aria-label={`${t.accounting.ruleSource} ${i + 1}`} value={l.amount_source} onChange={(e) => setLine(i, { amount_source: e.target.value as LineForm["amount_source"] })}>
                        {Object.entries(t.accounting.ruleSources).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                      </select>
                    </td>
                    <td><input aria-label={`${t.accounting.lMemo} ${i + 1}`} value={l.memo} maxLength={300} onChange={(e) => setLine(i, { memo: e.target.value })} /></td>
                    <td><Button type="button" variant="secondary" className="btn-sm" aria-label={`${t.accounting.removeLine} ${i + 1}`} disabled={form.lines.length <= 2} onClick={() => setForm({ ...form, lines: form.lines.filter((_, j) => j !== i) })}>✕</Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ marginTop: 8 }}>
            <Button type="button" variant="secondary" className="btn-sm" disabled={form.lines.length >= 50} onClick={() => setForm({ ...form, lines: [...form.lines, blankLine("credit")] })}>{t.accounting.addLine}</Button>
          </div>
        </fieldset>
        <p className={balanced ? "hint" : "field-error"} role="status" data-testid="rule-balance">{balanced ? t.accounting.balanced : t.accounting.ruleBalancedHint}</p>
        {editable ? (
          <div className="drawer-actions" style={{ alignItems: "center" }}>
            <Button onClick={() => void save()} loading={busy === "save"} disabled={!dirty || !complete || !balanced}>{t.accounting.save}</Button>
            {saved && !dirty ? <span className="hint" role="status">✓</span> : null}
          </div>
        ) : null}
      </section>

      <Modal open={dlg === "approve"} title={t.accounting.ruleApproveTitle} onClose={() => setDlg(null)}
        footer={<><Button variant="secondary" onClick={() => setDlg(null)}>{t.accounting.cancel}</Button><Button loading={busy === "approve"} disabled={!basis.trim()} onClick={() => void approve()}>{t.accounting.ruleApprove}</Button></>}>
        <p className="muted">{t.accounting.ruleApproveBody}</p>
        <TextArea id="r-basis" label={t.accounting.ruleBasis} value={basis} maxLength={1000} onChange={(e) => setBasis(e.target.value)} />
      </Modal>
      <ConfirmDialog open={dlg === "retire"} title={t.accounting.ruleRetireTitle} body={t.accounting.ruleRetireBody} confirmLabel={t.accounting.ruleRetire} busy={busy === "retire"} onCancel={() => setDlg(null)} onConfirm={() => void retire()} />
      <ConfirmDialog open={dlg === "delete"} title={t.accounting.ruleDeleteTitle} body={t.accounting.ruleDeleteBody} confirmLabel={t.accounting.ruleDelete} busy={busy === "delete"} onCancel={() => setDlg(null)} onConfirm={() => void remove()} />
    </div>
  );
}
