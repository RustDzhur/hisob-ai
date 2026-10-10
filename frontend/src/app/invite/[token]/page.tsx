"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthLayout } from "@/components/AuthLayout";
import { Banner, Button, ErrorState, Skeleton } from "@/components/ui";
import { errorText } from "@/i18n/errors";
import { useT } from "@/i18n/LocaleProvider";
import { api, ApiError, type InvitationPreview, type Organization, toApiError } from "@/lib/api";
import { rememberOrg, useSession } from "@/lib/session";

export default function InvitePage() {
  const t = useT();
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
      .catch((e) => setError(toApiError(e)));
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
      setAcceptError(toApiError(e));
    } finally {
      setBusy(false);
    }
  }

  const nextQ = `?next=${encodeURIComponent(`/invite/${token}`)}`;
  let body: React.ReactNode;

  if (error?.status === 404) body = <Banner>{t.invite.notFound}</Banner>;
  else if (error) body = <ErrorState error={error} />;
  else if (!preview || state.status === "loading") body = <Skeleton />;
  else if (preview.state !== "pending") {
    body = <Banner>{{ expired: t.invite.expired, revoked: t.invite.revoked, accepted: t.invite.accepted }[preview.state]}</Banner>;
  } else {
    body = (
      <>
        <p>
          {t.invite.invitedTo} <strong>{preview.organization_name}</strong> {t.invite.asRole}{" "}
          <strong>{t.roles[preview.role] ?? preview.role}</strong>.
        </p>
        <p className="muted">{t.invite.forEmail}: {preview.email}</p>
        {state.status === "ready" ? (
          <>
            <p className="muted">{t.invite.loggedAs}: {state.user.email}</p>
            {acceptError ? <Banner>{errorText(t, acceptError)}</Banner> : null}
            <Button onClick={accept} loading={busy} style={{ width: "100%" }}>{t.invite.accept}</Button>
          </>
        ) : (
          <>
            <p className="muted">{t.invite.needLogin}</p>
            <div className="row-actions">
              <Link className="btn btn-primary" href={`/login${nextQ}`}>{t.invite.login}</Link>
              <Link className="btn btn-secondary" href={`/register${nextQ}`}>{t.invite.register}</Link>
            </div>
          </>
        )}
      </>
    );
  }

  return <AuthLayout title={t.invite.title}>{body}</AuthLayout>;
}
