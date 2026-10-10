"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { AuthLayout } from "@/components/AuthLayout";
import { Banner, Button, ErrorState, Field, Skeleton } from "@/components/ui";
import { errorText, fieldErrorMap } from "@/i18n/errors";
import { useT } from "@/i18n/LocaleProvider";
import { api, ApiError, type Organization } from "@/lib/api";
import { newIdempotencyKey } from "@/lib/id";
import { rememberOrg, useSession } from "@/lib/session";

export default function OnboardingPage() {
  const t = useT();
  const router = useRouter();
  const { state, reload } = useSession();
  const [name, setName] = useState("");
  const [tin, setTin] = useState("");
  const [timezone, setTimezone] = useState("Asia/Tashkent");
  const [currency, setCurrency] = useState("UZS");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  // Один ключ на одну попытку создания: повторная отправка не создаст вторую организацию.
  const idemKey = useRef(newIdempotencyKey());

  useEffect(() => {
    if (state.status === "anon") router.replace("/login");
  }, [state, router]);

  if (state.status === "error") return <main className="content"><ErrorState error={state.error} onRetry={reload} /></main>;
  if (state.status !== "ready") return <main className="content"><Skeleton /></main>;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (name.trim().length < 2) {
      setErrors({ name: t.onboarding.nameShort });
      return;
    }
    setErrors({});
    setBusy(true);
    try {
      const org = await api<Organization>("/organizations", {
        method: "POST",
        idempotencyKey: idemKey.current,
        body: { name, tin: tin.trim() || null, timezone, base_currency: currency },
      });
      rememberOrg(org.id);
      await reload();
      router.replace(`/o/${org.id}`);
    } catch (err) {
      const fe = fieldErrorMap(t, err);
      setErrors(fe);
      if (err instanceof ApiError && Object.keys(fe).length === 0) setFormError(err);
      // тело изменилось -> нужен новый ключ идемпотентности
      if (err instanceof ApiError && err.code === "idempotency_key_reuse") idemKey.current = newIdempotencyKey();
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout title={t.onboarding.title}>
      <p className="muted">{t.onboarding.lead}</p>
      <form onSubmit={onSubmit} noValidate>
        {formError ? <Banner>{errorText(t, formError)}</Banner> : null}
        <Field id="name" label={t.onboarding.name} value={name} onChange={(e) => setName(e.target.value)} error={errors.name} />
        <Field id="tin" label={t.onboarding.tin} inputMode="numeric" value={tin} onChange={(e) => setTin(e.target.value)}
          error={errors.tin} hint={t.onboarding.tinHint} />
        <Field id="timezone" label={t.onboarding.timezone} value={timezone} onChange={(e) => setTimezone(e.target.value)} error={errors.timezone} />
        <Field id="currency" label={t.onboarding.currency} maxLength={3} value={currency}
          onChange={(e) => setCurrency(e.target.value.toUpperCase())} error={errors.base_currency} />
        <Button type="submit" loading={busy} style={{ width: "100%" }}>
          {t.onboarding.submit}
        </Button>
      </form>
    </AuthLayout>
  );
}
