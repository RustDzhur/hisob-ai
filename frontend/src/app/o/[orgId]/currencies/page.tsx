"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Banner, Button, ConfirmDialog, ErrorState, Skeleton } from "@/components/ui";
import { errorText } from "@/i18n/errors";
import { useLocale, useT } from "@/i18n/LocaleProvider";
import { pickName } from "@/i18n/names";
import { api, ApiError, toApiError, type Currency } from "@/lib/api";
import { useSession } from "@/lib/session";

export default function CurrenciesPage() {
  const t = useT();
  const { locale } = useLocale();
  const { orgId } = useParams<{ orgId: string }>();
  const { state } = useSession();
  const org = state.status === "ready" ? state.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("catalog.read");
  const canManage = !!org?.permissions.includes("catalog.manage");

  const [items, setItems] = useState<Currency[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Currency | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setItems(await api<Currency[]>(`/organizations/${orgId}/currencies`));
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

  async function toggle(c: Currency, enabled: boolean) {
    setActionError(null);
    setBusy(c.code);
    try {
      setItems(
        await api<Currency[]>(`/organizations/${orgId}/currencies/${c.code}`, {
          method: "PUT",
          body: { enabled },
        }),
      );
    } catch (e) {
      setActionError(toApiError(e));
    } finally {
      setBusy(null);
      setConfirm(null);
    }
  }

  return (
    <div className="stack">
      <div>
        <h1>{t.catalog.currencies}</h1>
        <p className="muted">{t.catalog.currenciesLead}</p>
      </div>
      {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}
      <section className="card" aria-label={t.catalog.currencies}>
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>{t.catalog.cCode}</th>
                <th>{t.catalog.cName}</th>
                <th>{t.catalog.cDecimals}</th>
                <th>{t.catalog.cStatus}</th>
                {canManage ? <th /> : null}
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.code}>
                  <td data-label={t.catalog.cCode}><strong>{c.code}</strong></td>
                  <td data-label={t.catalog.cName}>{pickName(c, locale)}</td>
                  <td data-label={t.catalog.cDecimals}>{c.minor_units}</td>
                  <td data-label={t.catalog.cStatus}>
                    {c.is_base ? (
                      <span className="badge badge-ok">{t.catalog.base}</span>
                    ) : (
                      <span className={`badge ${c.enabled ? "badge-ok" : "badge-muted"}`}>
                        {c.enabled ? t.catalog.enabled : t.catalog.disabled}
                      </span>
                    )}
                  </td>
                  {canManage ? (
                    <td>
                      {c.is_base ? null : c.enabled ? (
                        <Button variant="secondary" className="btn-sm" disabled={busy === c.code}
                          aria-label={`${t.catalog.disable}: ${c.code}`} onClick={() => setConfirm(c)}>
                          {t.catalog.disable}
                        </Button>
                      ) : (
                        <Button variant="secondary" className="btn-sm" loading={busy === c.code}
                          aria-label={`${t.catalog.enable}: ${c.code}`} onClick={() => void toggle(c, true)}>
                          {t.catalog.enable}
                        </Button>
                      )}
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <ConfirmDialog
        open={confirm !== null}
        title={t.catalog.disableTitle}
        body={t.catalog.disableBody}
        confirmLabel={t.catalog.confirm}
        busy={busy !== null}
        onCancel={() => setConfirm(null)}
        onConfirm={() => confirm && void toggle(confirm, false)}
      />
    </div>
  );
}
