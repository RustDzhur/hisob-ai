"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Banner, Button, ConfirmDialog, ErrorState, Field, Skeleton } from "@/components/ui";
import { ru } from "@/i18n/ru";
import {
  api,
  ApiError,
  fieldErrors,
  type Invitation,
  type InvitationCreated,
  type Member,
  type Role,
} from "@/lib/api";
import { useSession } from "@/lib/session";

const fmt = (iso: string) => new Date(iso).toLocaleDateString("ru-RU");

type Pending = { kind: "suspend"; member: Member } | { kind: "revoke"; invitation: Invitation } | null;

export default function MembersPage() {
  const { orgId } = useParams<{ orgId: string }>();
  const { state } = useSession();
  const org = state.status === "ready" ? state.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("members.read");
  const canManage = !!org?.permissions.includes("members.manage");
  const meId = state.status === "ready" ? state.user.id : "";

  const [members, setMembers] = useState<Member[] | null>(null);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending>(null);

  const [showInvite, setShowInvite] = useState(false);
  const [email, setEmail] = useState("");
  const [roleCode, setRoleCode] = useState("");
  const [inviteErrors, setInviteErrors] = useState<Record<string, string>>({});
  const [inviteBusy, setInviteBusy] = useState(false);
  const [created, setCreated] = useState<InvitationCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const linkRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const m = await api<Member[]>(`/organizations/${orgId}/members`);
      setMembers(m);
      if (canManage) {
        setInvitations(await api<Invitation[]>(`/organizations/${orgId}/invitations`));
        const r = await api<Role[]>("/roles");
        setRoles(r);
        setRoleCode((cur) => cur || r.find((x) => x.code === "accountant")?.code || r[0]?.code || "");
      }
    } catch (e) {
      setLoadError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Неизвестная ошибка."));
    }
  }, [orgId, canManage]);

  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);

  if (state.status !== "ready") return <Skeleton />;
  if (!canRead) {
    return (
      <div className="card">
        <h1>{ru.members.noAccessTitle}</h1>
        <p className="muted">{ru.members.noAccessBody}</p>
      </div>
    );
  }
  if (loadError) return <ErrorState error={loadError} onRetry={load} />;
  if (!members) return <Skeleton lines={5} />;

  const myRole = org?.role ?? "";
  // org_admin не может назначать/менять владельцев (сервер проверяет то же самое).
  const assignable = (r: Role) => myRole === "owner" || r.code !== "owner";
  const modifiable = (m: Member) => canManage && (myRole === "owner" || m.role !== "owner");

  async function patch(m: Member, body: { role_code?: string; status?: "active" | "suspended" }) {
    setActionError(null);
    setBusyId(m.id);
    try {
      await api<Member>(`/organizations/${orgId}/members/${m.id}`, { method: "PATCH", body });
      await load();
    } catch (e) {
      setActionError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Неизвестная ошибка."));
      await load();
    } finally {
      setBusyId(null);
      setPending(null);
    }
  }

  async function revoke(inv: Invitation) {
    setActionError(null);
    setBusyId(inv.id);
    try {
      await api<void>(`/organizations/${orgId}/invitations/${inv.id}`, { method: "DELETE" });
      await load();
    } catch (e) {
      setActionError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Неизвестная ошибка."));
    } finally {
      setBusyId(null);
      setPending(null);
    }
  }

  async function createInvite(e: React.FormEvent) {
    e.preventDefault();
    setInviteErrors({});
    setCreated(null);
    if (!email.trim()) {
      setInviteErrors({ email: ru.auth.required });
      return;
    }
    setInviteBusy(true);
    try {
      const inv = await api<InvitationCreated>(`/organizations/${orgId}/invitations`, {
        method: "POST",
        body: { email, role_code: roleCode },
      });
      setCreated(inv);
      setCopied(false);
      setEmail("");
      await load();
    } catch (err) {
      const fe = fieldErrors(err);
      setInviteErrors(Object.keys(fe).length ? fe : { email: err instanceof ApiError ? err.message : "Ошибка." });
    } finally {
      setInviteBusy(false);
    }
  }

  const inviteUrl = created ? `${window.location.origin}/invite/${created.token}` : "";
  async function copy() {
    try {
      await navigator.clipboard.writeText(inviteUrl);
    } catch {
      linkRef.current?.select(); // http без clipboard API: пользователь копирует вручную
    }
    setCopied(true);
  }

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{ru.members.title}</h1>
        {canManage ? (
          <Button onClick={() => setShowInvite((v) => !v)} aria-expanded={showInvite}>
            {ru.members.invite}
          </Button>
        ) : null}
      </div>

      {actionError ? <Banner>{actionError.message}</Banner> : null}

      {showInvite && canManage ? (
        <section className="card" aria-labelledby="invite-h">
          <h2 id="invite-h">{ru.members.inviteTitle}</h2>
          <form onSubmit={createInvite} noValidate>
            <Field id="invite-email" type="email" label={ru.members.email} value={email}
              onChange={(e) => setEmail(e.target.value)} error={inviteErrors.email ?? inviteErrors.role_code} />
            <div className="field">
              <label htmlFor="invite-role">{ru.members.role}</label>
              <select id="invite-role" value={roleCode} onChange={(e) => setRoleCode(e.target.value)}>
                {roles.filter(assignable).map((r) => (
                  <option key={r.code} value={r.code}>{ru.roles[r.code] ?? r.name}</option>
                ))}
              </select>
            </div>
            <Button type="submit" loading={inviteBusy}>{ru.members.send}</Button>
          </form>
          {created ? (
            <div style={{ marginTop: 16 }}>
              <Banner kind="info">{ru.members.linkTitle}: {created.email}</Banner>
              <div className="link-box">
                <input ref={linkRef} readOnly value={inviteUrl} aria-label="Ссылка-приглашение" onFocus={(e) => e.target.select()} />
                <Button variant="secondary" onClick={copy}>{copied ? ru.members.copied : ru.members.copy}</Button>
              </div>
              <p className="hint">{ru.members.linkNote}</p>
            </div>
          ) : null}
        </section>
      ) : null}

      <section className="card" aria-label={ru.members.title}>
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>{ru.members.colName}</th>
                <th>{ru.members.colRole}</th>
                <th>{ru.members.colStatus}</th>
                {canManage ? <th>{ru.members.colActions}</th> : null}
              </tr>
            </thead>
            <tbody>
              {members.map((m) => {
                const mine = m.user_id === meId;
                const editable = modifiable(m);
                return (
                  <tr key={m.id}>
                    <td data-label={ru.members.colName}>
                      {m.full_name}{mine ? <span className="cell-sub"> ({ru.members.you})</span> : null}
                      <div className="cell-sub">{m.email}</div>
                    </td>
                    <td data-label={ru.members.colRole}>
                      {editable ? (
                        <select className="inline-select" aria-label={`${ru.members.role}: ${m.email}`}
                          value={m.role} disabled={busyId === m.id}
                          onChange={(e) => void patch(m, { role_code: e.target.value })}>
                          {roles.filter((r) => assignable(r) || r.code === m.role).map((r) => (
                            <option key={r.code} value={r.code}>{ru.roles[r.code] ?? r.name}</option>
                          ))}
                        </select>
                      ) : (
                        ru.roles[m.role] ?? m.role
                      )}
                    </td>
                    <td data-label={ru.members.colStatus}>
                      <span className={`badge ${m.status === "active" ? "badge-ok" : "badge-warn"}`}>
                        {m.status === "active" ? ru.members.active : ru.members.suspended}
                      </span>
                    </td>
                    {canManage ? (
                      <td data-label={ru.members.colActions}>
                        <div className="row-actions">
                          {editable && !mine && m.status === "active" ? (
                            <Button variant="secondary" className="btn-sm" disabled={busyId === m.id}
                              onClick={() => setPending({ kind: "suspend", member: m })}>{ru.members.suspend}</Button>
                          ) : null}
                          {editable && m.status === "suspended" ? (
                            <Button variant="secondary" className="btn-sm" loading={busyId === m.id}
                              onClick={() => void patch(m, { status: "active" })}>{ru.members.reactivate}</Button>
                          ) : null}
                        </div>
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {canManage ? (
        <section className="card" aria-labelledby="pending-h">
          <h2 id="pending-h">{ru.members.pendingTitle}</h2>
          {invitations.length === 0 ? (
            <p className="muted">{ru.members.pendingEmpty}</p>
          ) : (
            <div className="table-wrap">
              <table className="tbl">
                <tbody>
                  {invitations.map((i) => (
                    <tr key={i.id}>
                      <td data-label="Email">{i.email}</td>
                      <td data-label={ru.members.colRole}>{ru.roles[i.role] ?? i.role}</td>
                      <td data-label={ru.members.expires}>
                        {i.expired ? <span className="badge badge-warn">{ru.members.expired}</span> : fmt(i.expires_at)}
                      </td>
                      <td>
                        <Button variant="secondary" className="btn-sm" disabled={busyId === i.id}
                          onClick={() => setPending({ kind: "revoke", invitation: i })}>{ru.members.revoke}</Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ) : null}

      <ConfirmDialog
        open={pending !== null}
        title={pending?.kind === "revoke" ? ru.members.revokeTitle : ru.members.suspendTitle}
        body={pending?.kind === "revoke" ? ru.members.revokeBody : ru.members.suspendBody}
        confirmLabel={ru.members.confirm}
        busy={busyId !== null}
        onCancel={() => setPending(null)}
        onConfirm={() => {
          if (pending?.kind === "suspend") void patch(pending.member, { status: "suspended" });
          if (pending?.kind === "revoke") void revoke(pending.invitation);
        }}
      />
    </div>
  );
}
