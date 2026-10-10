"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthLayout } from "@/components/AuthLayout";
import { Banner, Button, ErrorState, Skeleton } from "@/components/ui";
import { ru } from "@/i18n/ru";
import { api, ApiError, type InvitationPreview, type Organization } from "@/lib/api";
import { rememberOrg, useSession } from "@/lib/session";

export default function InvitePage() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const { state, reload } = useSession();
  const [preview, setPreview] = useState<InvitationPreview | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [acceptError, setAcceptError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<InvitationPreview>(`/invitations/${encodeURIComponent(token)}`)
      .then(setPreview)
      .catch((e) => setError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Неизвестная ошибка.")));
  }, [token]);

  async function accept() {
    setAcceptError(null);
    setBusy(true);
    try {
      const org = await api<Organization>("/invitations/accept", { method: "POST", body: { token } });
      rememberOrg(org.id);
      await reload();
      router.replace(`/o/${org.id}`);
    } catch (e) {
      setAcceptError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Неизвестная ошибка."));
    } finally {
      setBusy(false);
    }
  }

  const nextQ = `?next=${encodeURIComponent(`/invite/${token}`)}`;
  let body: React.ReactNode;

  if (error?.status === 404) body = <Banner>{ru.invite.notFound}</Banner>;
  else if (error) body = <ErrorState error={error} />;
  else if (!preview || state.status === "loading") body = <Skeleton />;
  else if (preview.state !== "pending") {
    body = <Banner>{{ expired: ru.invite.expired, revoked: ru.invite.revoked, accepted: ru.invite.accepted }[preview.state]}</Banner>;
  } else {
    body = (
      <>
        <p>
          {ru.invite.invitedTo} <strong>{preview.organization_name}</strong> {ru.invite.asRole}{" "}
          <strong>{ru.roles[preview.role] ?? preview.role}</strong>.
        </p>
        <p className="muted">{ru.invite.forEmail}: {preview.email}</p>
        {state.status === "ready" ? (
          <>
            <p className="muted">{ru.invite.loggedAs}: {state.user.email}</p>
            {acceptError ? <Banner>{acceptError.message}</Banner> : null}
            <Button onClick={accept} loading={busy} style={{ width: "100%" }}>{ru.invite.accept}</Button>
          </>
        ) : (
          <>
            <p className="muted">{ru.invite.needLogin}</p>
            <div className="row-actions">
              <Link className="btn btn-primary" href={`/login${nextQ}`}>{ru.invite.login}</Link>
              <Link className="btn btn-secondary" href={`/register${nextQ}`}>{ru.invite.register}</Link>
            </div>
          </>
        )}
      </>
    );
  }

  return <AuthLayout title={ru.invite.title}>{body}</AuthLayout>;
}
