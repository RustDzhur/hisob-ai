"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Banner, Button, ConfirmDialog, ErrorState, Field, Skeleton } from "@/components/ui";
import { errorText, fieldErrorMap } from "@/i18n/errors";
import { useFormat, useT } from "@/i18n/LocaleProvider";
import { api, ApiError, toApiError, type SessionInfo } from "@/lib/api";
import { useSession } from "@/lib/session";


export default function AccountPage() {
  const t = useT();
  const { dateTime: fmt } = useFormat();
  const router = useRouter();
  const { state, logout } = useSession();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<ApiError | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  const [sessions, setSessions] = useState<SessionInfo[] | null>(null);
  const [sessionsError, setSessionsError] = useState<ApiError | null>(null);
  const [sessionBusy, setSessionBusy] = useState<string | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);
  const [allBusy, setAllBusy] = useState(false);

  const loadSessions = useCallback(async () => {
    setSessionsError(null);
    try {
      setSessions(await api<SessionInfo[]>("/auth/sessions"));
    } catch (e) {
      setSessionsError(toApiError(e));
    }
  }, []);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  if (state.status !== "ready") return <Skeleton />;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    setDone(false);
    const local: Record<string, string> = {};
    if (!current) local.current_password = t.auth.required;
    if (next.length < 10) local.new_password = t.auth.passwordShort;
    if (confirm !== next) local.confirm = t.account.mismatch;
    setErrors(local);
    if (Object.keys(local).length) return;
    setBusy(true);
    try {
      await api<void>("/auth/change-password", {
        method: "POST",
        body: { current_password: current, new_password: next },
      });
      setCurrent(""); setNext(""); setConfirm("");
      setDone(true);
      await loadSessions();
    } catch (err) {
      const fe = fieldErrorMap(t, err);
      setErrors(fe);
      if (err instanceof ApiError && Object.keys(fe).length === 0) setFormError(err);
    } finally {
      setBusy(false);
    }
  }

  async function endSession(s: SessionInfo) {
    setSessionBusy(s.id);
    try {
      await api<void>(`/auth/sessions/${s.id}`, { method: "DELETE" });
      if (s.current) {
        await logout();
        router.replace("/login");
        return;
      }
      await loadSessions();
    } catch (e) {
      setSessionsError(toApiError(e));
    } finally {
      setSessionBusy(null);
    }
  }

  async function endAll() {
    setAllBusy(true);
    try {
      await api<void>("/auth/logout-all", { method: "POST" });
      await logout();
      router.replace("/login");
    } catch (e) {
      setSessionsError(toApiError(e));
      setConfirmAll(false);
    } finally {
      setAllBusy(false);
    }
  }

  return (
    <div className="stack">
      <div>
        <h1>{t.account.title}</h1>
        <p className="muted">{state.user.full_name} · {state.user.email}</p>
      </div>

      <section className="card" aria-labelledby="pw-h">
        <h2 id="pw-h">{t.account.passwordTitle}</h2>
        <form onSubmit={submit} noValidate style={{ maxWidth: 400 }}>
          {formError ? <Banner>{errorText(t, formError)}</Banner> : null}
          {done ? <Banner kind="info">{t.account.changed}</Banner> : null}
          <Field id="cur-pw" type="password" label={t.account.current} autoComplete="current-password"
            value={current} onChange={(e) => setCurrent(e.target.value)} error={errors.current_password} />
          <Field id="new-pw" type="password" label={t.account.newPassword} autoComplete="new-password"
            value={next} onChange={(e) => setNext(e.target.value)} error={errors.new_password} hint={t.auth.passwordHint} />
          <Field id="conf-pw" type="password" label={t.account.confirm} autoComplete="new-password"
            value={confirm} onChange={(e) => setConfirm(e.target.value)} error={errors.confirm} />
          <Button type="submit" loading={busy}>{t.account.change}</Button>
        </form>
      </section>

      <section className="card" aria-labelledby="sess-h">
        <div className="page-head">
          <div>
            <h2 id="sess-h">{t.account.sessionsTitle}</h2>
            <p className="muted" style={{ margin: 0 }}>{t.account.sessionsLead}</p>
          </div>
          <Button variant="secondary" onClick={() => setConfirmAll(true)}>{t.account.logoutAll}</Button>
        </div>
        {sessionsError ? <ErrorState error={sessionsError} onRetry={loadSessions} /> : null}
        {!sessions && !sessionsError ? <Skeleton /> : null}
        {sessions ? (
          <div className="table-wrap">
            <table className="tbl">
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.id}>
                    <td>
                      {(s.user_agent || t.account.unknownDevice).slice(0, 80)}
                      {s.current ? <> <span className="badge badge-ok">{t.account.currentSession}</span></> : null}
                      <div className="cell-sub">{s.ip ?? "—"}</div>
                    </td>
                    <td data-label={t.account.started}>{fmt(s.created_at)}</td>
                    <td data-label={t.account.until}>{fmt(s.expires_at)}</td>
                    <td>
                      <Button variant="secondary" className="btn-sm" loading={sessionBusy === s.id}
                        aria-label={`${t.account.end}: ${s.current ? t.account.currentSession : s.ip ?? s.id}`}
                        onClick={() => void endSession(s)}>{t.account.end}</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <ConfirmDialog open={confirmAll} title={t.account.logoutAllTitle} body={t.account.logoutAllBody}
        confirmLabel={t.members.confirm} busy={allBusy} onCancel={() => setConfirmAll(false)} onConfirm={() => void endAll()} />
    </div>
  );
}
