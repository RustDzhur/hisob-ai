"use client";

import { useCallback, useEffect, useState } from "react";
import { Banner, Button, Field } from "@/components/ui";
import { errorText, fill } from "@/i18n/errors";
import { useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type MfaSetup, type MfaStatus } from "@/lib/api";

/** Второй фактор (TOTP): включение, коды восстановления, выключение. Ключ показывается текстом (без QR-библиотек). */
export function MfaSection() {
  const t = useT();
  const [status, setStatus] = useState<MfaStatus | null>(null);
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await api<MfaStatus>("/auth/mfa"));
    } catch (e) {
      setError(toApiError(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(false);
    }
  }

  if (!status) return error ? <Banner>{errorText(t, error)}</Banner> : null;
  return (
    <section className="card" aria-labelledby="mfa-h" data-testid="mfa">
      <h2 id="mfa-h">{t.account.mfaTitle}</h2>
      <p className="hint">{t.account.mfaLead}</p>
      {status.required_for_admins ? <p className="hint">{t.account.mfaRequiredAdmins}</p> : null}
      {error ? <Banner>{errorText(t, error)}</Banner> : null}
      {notice ? <p role="status">{notice}</p> : null}
      {!status.available ? <p className="muted">{t.account.mfaUnavailable}</p> : null}

      {codes ? (
        <div data-testid="mfa-codes">
          <h3>{t.account.mfaCodesTitle}</h3>
          <p className="hint">{t.account.mfaCodesNote}</p>
          <ul className="plain" style={{ fontFamily: "monospace" }}>{codes.map((c) => <li key={c}>{c}</li>)}</ul>
          <Button onClick={() => { setCodes(null); void load(); }}>{t.account.mfaCodesSaved}</Button>
        </div>
      ) : status.enabled ? (
        <>
          <p><span className="badge badge-ok">{t.account.mfaOn}</span> {fill(t.account.mfaCodesLeft, { n: status.recovery_codes_left })}</p>
          <Field id="mfa-pw" type="password" label={t.account.mfaPassword} autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          <Field id="mfa-code" label={t.account.mfaCodeLabel} autoComplete="one-time-code" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} />
          <div className="toolbar">
            <Button variant="secondary" loading={busy} disabled={!password || !code} onClick={() => void run(async () => {
              const r = await api<{ recovery_codes: string[] }>("/auth/mfa/recovery-codes", { method: "POST", body: { password, code } });
              setCodes(r.recovery_codes); setCode(""); setPassword(""); setNotice(t.account.mfaRegenerated);
            })}>{t.account.mfaRegenerate}</Button>
            <Button variant="secondary" loading={busy} disabled={!password || !code} onClick={() => void run(async () => {
              await api<void>("/auth/mfa/disable", { method: "POST", body: { password, code } });
              setCode(""); setPassword(""); await load();
            })}>{t.account.mfaDisable}</Button>
          </div>
        </>
      ) : setup ? (
        <div data-testid="mfa-setup">
          <h3>{t.account.mfaSetupTitle}</h3>
          <p className="hint">{t.account.mfaSetupNote}</p>
          <p>{t.account.mfaSecret}: <code data-testid="mfa-secret">{setup.secret}</code></p>
          <p className="hint">{t.account.mfaUri}: <a href={setup.otpauth_uri}>{setup.otpauth_uri}</a></p>
          <Field id="mfa-code" label={t.account.mfaCodeLabel} autoComplete="one-time-code" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} />
          <Button loading={busy} disabled={!code} onClick={() => void run(async () => {
            const r = await api<{ recovery_codes: string[] }>("/auth/mfa/enable", { method: "POST", body: { code } });
            setCodes(r.recovery_codes); setSetup(null); setCode("");
          })}>{t.account.mfaConfirm}</Button>
        </div>
      ) : status.available ? (
        <>
          <p><span className="badge badge-muted">{t.account.mfaOff}</span></p>
          <Button loading={busy} onClick={() => void run(async () => setSetup(await api<MfaSetup>("/auth/mfa/setup", { method: "POST" })))}>{t.account.mfaStart}</Button>
        </>
      ) : null}
    </section>
  );
}
