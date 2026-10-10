"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Banner, Button, ErrorState, Field, Skeleton } from "@/components/ui";
import { errorText, fieldErrorMap } from "@/i18n/errors";
import { useLocale, useT } from "@/i18n/LocaleProvider";
import { pickName } from "@/i18n/names";
import { api, toApiError, type ApiError, type Unit } from "@/lib/api";
import { useSession } from "@/lib/session";

type Names = { name_uz: string; name_ru: string; name_en: string };
const emptyNames: Names = { name_uz: "", name_ru: "", name_en: "" };
const toBody = (n: Names) => ({
  name_uz: n.name_uz.trim() || null,
  name_ru: n.name_ru.trim() || null,
  name_en: n.name_en.trim() || null,
});

export default function UnitsPage() {
  const t = useT();
  const { locale } = useLocale();
  const { orgId } = useParams<{ orgId: string }>();
  const { state } = useSession();
  const org = state.status === "ready" ? state.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("catalog.read");
  const canManage = !!org?.permissions.includes("catalog.manage");

  const [items, setItems] = useState<Unit[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [code, setCode] = useState("");
  const [names, setNames] = useState<Names>(emptyNames);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formBusy, setFormBusy] = useState(false);

  const [editing, setEditing] = useState<string | null>(null);
  const [editNames, setEditNames] = useState<Names>(emptyNames);
  const [editError, setEditError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setItems(await api<Unit[]>(`/organizations/${orgId}/units`));
    } catch (e) {
      setError(toApiError(e));
    }
  }, [orgId]);

  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);

  if (state.status !== "ready") return <Skeleton />;
  if (!canRead) {
    return (
      <div className="card">
        <h1>{t.catalog.noAccessTitle}</h1>
        <p className="muted">{t.catalog.noAccessBody}</p>
      </div>
    );
  }
  if (error) return <ErrorState error={error} onRetry={load} />;
  if (!items) return <Skeleton lines={6} />;

  async function create(e: React.FormEvent) {
    e.preventDefault();
    const local: Record<string, string> = {};
    if (!code.trim()) local.code = t.auth.required;
    if (!names.name_uz.trim() && !names.name_ru.trim() && !names.name_en.trim()) local.names = t.fieldErrors.unit_name_required;
    setErrors(local);
    if (Object.keys(local).length) return;
    setFormBusy(true);
    try {
      await api<Unit>(`/organizations/${orgId}/units`, { method: "POST", body: { code, ...toBody(names) } });
      setCode(""); setNames(emptyNames); setShowForm(false);
      await load();
    } catch (err) {
      const fe = fieldErrorMap(t, err);
      // ошибка «нужно название» приходит без привязки к полю — показываем под названиями
      const byCode = (err as ApiError).details?.find((d) => d.code === "unit_name_required");
      if (byCode) fe.names = t.fieldErrors.unit_name_required;
      setErrors(Object.keys(fe).length ? fe : { code: errorText(t, toApiError(err)) });
    } finally {
      setFormBusy(false);
    }
  }

  async function patch(u: Unit, body: Record<string, unknown>) {
    setActionError(null);
    setBusy(u.id);
    try {
      await api<Unit>(`/organizations/${orgId}/units/${u.id}`, { method: "PATCH", body });
      setEditing(null);
      await load();
    } catch (err) {
      const e = toApiError(err);
      if (editing === u.id) setEditError(e.details.some((d) => d.code === "unit_name_required") ? t.fieldErrors.unit_name_required : errorText(t, e));
      else setActionError(e);
    } finally {
      setBusy(null);
    }
  }

  const nameFields = (n: Names, set: (v: Names) => void, prefix: string) => (
    <>
      <Field id={`${prefix}-uz`} label={t.catalog.nameUz} value={n.name_uz} maxLength={100}
        onChange={(e) => set({ ...n, name_uz: e.target.value })} />
      <Field id={`${prefix}-ru`} label={t.catalog.nameRu} value={n.name_ru} maxLength={100}
        onChange={(e) => set({ ...n, name_ru: e.target.value })} />
      <Field id={`${prefix}-en`} label={t.catalog.nameEn} value={n.name_en} maxLength={100}
        onChange={(e) => set({ ...n, name_en: e.target.value })} />
    </>
  );

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>{t.catalog.units}</h1>
          <p className="muted" style={{ margin: 0 }}>{t.catalog.unitsLead}</p>
        </div>
        {canManage ? (
          <Button onClick={() => setShowForm((v) => !v)} aria-expanded={showForm}>{t.catalog.addUnit}</Button>
        ) : null}
      </div>
      {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}

      {showForm && canManage ? (
        <section className="card" aria-labelledby="unit-new">
          <h2 id="unit-new">{t.catalog.newUnit}</h2>
          <form onSubmit={create} noValidate style={{ maxWidth: 420 }}>
            <Field id="u-code" label={t.catalog.codeLabel} value={code} maxLength={16}
              onChange={(e) => setCode(e.target.value)} error={errors.code} hint={t.catalog.codeHint} />
            {nameFields(names, setNames, "u")}
            {errors.names ? <p className="field-error" role="alert">{errors.names}</p> : <p className="hint">{t.catalog.namesHint}</p>}
            <div style={{ height: 12 }} />
            <Button type="submit" loading={formBusy}>{t.catalog.createUnit}</Button>
          </form>
        </section>
      ) : null}

      <section className="card" aria-label={t.catalog.units}>
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>{t.catalog.uCode}</th>
                <th>{t.catalog.uName}</th>
                <th>{t.catalog.uType}</th>
                <th>{t.catalog.uStatus}</th>
                {canManage ? <th>{t.catalog.uActions}</th> : null}
              </tr>
            </thead>
            <tbody>
              {items.map((u) => (
                <tr key={u.id}>
                  <td data-label={t.catalog.uCode}><strong>{u.code}</strong></td>
                  <td data-label={t.catalog.uName}>
                    {editing === u.id ? (
                      <div>
                        {nameFields(editNames, setEditNames, `e-${u.id}`)}
                        {editError ? <p className="field-error" role="alert">{editError}</p> : null}
                        <div className="row-actions">
                          <Button className="btn-sm" loading={busy === u.id} onClick={() => void patch(u, toBody(editNames))}>{t.catalog.saveNames}</Button>
                          <Button variant="secondary" className="btn-sm" onClick={() => setEditing(null)}>{t.catalog.cancel}</Button>
                        </div>
                      </div>
                    ) : (
                      pickName(u, locale)
                    )}
                  </td>
                  <td data-label={t.catalog.uType}>{u.is_system ? t.catalog.system : t.catalog.custom}</td>
                  <td data-label={t.catalog.uStatus}>
                    <span className={`badge ${u.is_active ? "badge-ok" : "badge-muted"}`}>
                      {u.is_active ? t.catalog.active : t.catalog.inactive}
                    </span>
                  </td>
                  {canManage ? (
                    <td data-label={t.catalog.uActions}>
                      {u.is_system ? null : (
                        <div className="row-actions">
                          <Button variant="secondary" className="btn-sm" aria-label={`${t.catalog.editNames}: ${u.code}`}
                            onClick={() => { setEditing(u.id); setEditError(null); setEditNames({ name_uz: u.name_uz ?? "", name_ru: u.name_ru ?? "", name_en: u.name_en ?? "" }); }}>
                            {t.catalog.editNames}
                          </Button>
                          <Button variant="secondary" className="btn-sm" disabled={busy === u.id}
                            aria-label={`${u.is_active ? t.catalog.deactivate : t.catalog.activate}: ${u.code}`}
                            onClick={() => void patch(u, { is_active: !u.is_active })}>
                            {u.is_active ? t.catalog.deactivate : t.catalog.activate}
                          </Button>
                        </div>
                      )}
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
