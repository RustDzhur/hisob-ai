import { useEffect, useRef } from "react";
import { ru } from "@/i18n/ru";
import type { ApiError } from "@/lib/api";

export function Button({
  loading,
  variant = "primary",
  children,
  disabled,
  className,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean; variant?: "primary" | "secondary" }) {
  return (
    <button
      {...rest}
      className={`btn btn-${variant}${className ? ` ${className}` : ""}`}
      disabled={disabled || loading}
      aria-busy={loading}
    >
      {loading ? <span className="spinner" aria-hidden /> : null}
      {children}
    </button>
  );
}

export function Field({
  label,
  error,
  hint,
  id,
  ...input
}: React.InputHTMLAttributes<HTMLInputElement> & { label: string; error?: string; hint?: string; id: string }) {
  const describedBy = [error ? `${id}-err` : null, hint ? `${id}-hint` : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} aria-invalid={error ? true : undefined} aria-describedby={describedBy} {...input} />
      {hint && !error ? (
        <p id={`${id}-hint`} className="hint">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-err`} className="field-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function Banner({ kind = "error", children }: { kind?: "error" | "info"; children: React.ReactNode }) {
  return (
    <div className={`banner banner-${kind}`} role={kind === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <div className="state">
      <Banner>{error.message}</Banner>
      {error.correlationId ? (
        <p className="hint">
          {ru.common.errorId}: <code>{error.correlationId}</code>
        </p>
      ) : null}
      {onRetry ? (
        <Button variant="secondary" onClick={onRetry}>
          {ru.common.retry}
        </Button>
      ) : null}
    </div>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton" aria-busy aria-label={ru.common.loading}>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skeleton-line" />
      ))}
    </div>
  );
}

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  busy,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className="dialog" onCancel={onCancel} aria-labelledby="dlg-title">
      <h2 id="dlg-title">{title}</h2>
      <p className="muted">{body}</p>
      <div className="dialog-actions">
        <Button variant="secondary" onClick={onCancel}>
          {ru.members.cancel}
        </Button>
        <Button onClick={onConfirm} loading={busy}>
          {confirmLabel}
        </Button>
      </div>
    </dialog>
  );
}
