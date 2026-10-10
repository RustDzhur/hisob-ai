"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { AuthLayout } from "@/components/AuthLayout";
import { Banner, Button, Field } from "@/components/ui";
import { ru } from "@/i18n/ru";
import { api, ApiError, fieldErrors, type User } from "@/lib/api";
import { safeNext } from "@/lib/next";
import { useSession } from "@/lib/session";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validateAuth(
  mode: "login" | "register",
  v: { email: string; password: string; fullName: string },
): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!v.email.trim()) errors.email = ru.auth.required;
  else if (!EMAIL_RE.test(v.email.trim())) errors.email = ru.auth.invalidEmail;
  if (!v.password) errors.password = ru.auth.required;
  else if (mode === "register" && v.password.length < 10) errors.password = ru.auth.passwordShort;
  if (mode === "register" && !v.fullName.trim()) errors.full_name = ru.auth.required;
  return errors;
}

export function AuthForm({ mode }: { mode: "login" | "register" }) {
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
    const local = validateAuth(mode, { email, password, fullName });
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
      const fe = fieldErrors(err);
      setErrors(fe);
      if (err instanceof ApiError && Object.keys(fe).length === 0) setFormError(err);
      else if (!(err instanceof ApiError)) setFormError(new ApiError(0, "unknown", "Неизвестная ошибка."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout title={isLogin ? ru.auth.loginTitle : ru.auth.registerTitle}>
      <form onSubmit={onSubmit} noValidate>
        {formError ? <Banner>{formError.message}</Banner> : null}
        {!isLogin ? (
          <Field id="full_name" label={ru.auth.fullName} autoComplete="name" value={fullName}
            onChange={(e) => setFullName(e.target.value)} error={errors.full_name} />
        ) : null}
        <Field id="email" type="email" label={ru.auth.email} autoComplete="email" value={email}
          onChange={(e) => setEmail(e.target.value)} error={errors.email} />
        <Field id="password" type="password" label={ru.auth.password}
          autoComplete={isLogin ? "current-password" : "new-password"} value={password}
          onChange={(e) => setPassword(e.target.value)} error={errors.password}
          hint={isLogin ? undefined : ru.auth.passwordHint} />
        <Button type="submit" loading={busy} style={{ width: "100%" }}>
          {isLogin ? ru.auth.loginSubmit : ru.auth.registerSubmit}
        </Button>
      </form>
      <p className="auth-alt">
        {isLogin ? ru.auth.noAccount : ru.auth.haveAccount}{" "}
        <Link href={`${isLogin ? "/register" : "/login"}${next ? `?next=${encodeURIComponent(next)}` : ""}`}>{isLogin ? ru.auth.toRegister : ru.auth.toLogin}</Link>
      </p>
    </AuthLayout>
  );
}
