"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { AuthLayout } from "@/components/AuthLayout";
import { Banner, Button, Field } from "@/components/ui";
import { errorText, fieldErrorMap } from "@/i18n/errors";
import type { Dict } from "@/i18n";
import { useT } from "@/i18n/LocaleProvider";
import { api, ApiError, toApiError, type User } from "@/lib/api";
import { safeNext } from "@/lib/next";
import { useSession } from "@/lib/session";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validateAuth(
  mode: "login" | "register",
  v: { email: string; password: string; fullName: string },
  t: Dict,
): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!v.email.trim()) errors.email = t.auth.required;
  else if (!EMAIL_RE.test(v.email.trim())) errors.email = t.auth.invalidEmail;
  if (!v.password) errors.password = t.auth.required;
  else if (mode === "register" && v.password.length < 10) errors.password = t.auth.passwordShort;
  if (mode === "register" && !v.fullName.trim()) errors.full_name = t.auth.required;
  return errors;
}

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const t = useT();
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const { reload } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const isLogin = mode === "login";

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    const local = validateAuth(mode, { email, password, fullName }, t);
    setErrors(local);
    if (Object.keys(local).length > 0) return;
    setBusy(true);
    try {
      await api<User>(isLogin ? "/auth/login" : "/auth/register", {
        method: "POST",
        body: isLogin ? { email, password } : { email, password, full_name: fullName },
      });
      await reload();
      router.replace(next ?? "/");
    } catch (err) {
      const fe = fieldErrorMap(t, err);
      setErrors(fe);
      if (Object.keys(fe).length === 0) setFormError(toApiError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout title={isLogin ? t.auth.loginTitle : t.auth.registerTitle}>
      <form onSubmit={onSubmit} noValidate>
        {formError ? <Banner>{errorText(t, formError)}</Banner> : null}
        {!isLogin ? (
          <Field id="full_name" label={t.auth.fullName} autoComplete="name" value={fullName}
            onChange={(e) => setFullName(e.target.value)} error={errors.full_name} />
        ) : null}
        <Field id="email" type="email" label={t.auth.email} autoComplete="email" value={email}
          onChange={(e) => setEmail(e.target.value)} error={errors.email} />
        <Field id="password" type="password" label={t.auth.password}
          autoComplete={isLogin ? "current-password" : "new-password"} value={password}
          onChange={(e) => setPassword(e.target.value)} error={errors.password}
          hint={isLogin ? undefined : t.auth.passwordHint} />
        <Button type="submit" loading={busy} style={{ width: "100%" }}>
          {isLogin ? t.auth.loginSubmit : t.auth.registerSubmit}
        </Button>
      </form>
      <p className="auth-alt">
        {isLogin ? t.auth.noAccount : t.auth.haveAccount}{" "}
        <Link href={`${isLogin ? "/register" : "/login"}${next ? `?next=${encodeURIComponent(next)}` : ""}`}>{isLogin ? t.auth.toRegister : t.auth.toLogin}</Link>
      </p>
    </AuthLayout>
  );
}
