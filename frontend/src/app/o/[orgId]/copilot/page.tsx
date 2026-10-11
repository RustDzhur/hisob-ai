"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Banner, Button, Skeleton } from "@/components/ui";
import { errorText, fill } from "@/i18n/errors";
import { useLocale, useT } from "@/i18n/LocaleProvider";
import {
  api, toApiError, type ApiError, type CopilotConversation, type CopilotConversationDetail, type CopilotMessage, type CopilotStatus,
} from "@/lib/api";
import { useSession } from "@/lib/session";

const MAX_LEN = 2000;
const POLL_MS = 1500;
const POLL_LIMIT = 130; // ~3 минуты: дольше воркер не работает над вопросом

export default function CopilotPage() {
  const t = useT();
  const { locale } = useLocale();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canUse = !!org?.permissions.includes("copilot.use");
  const base = `/organizations/${orgId}/copilot`;

  const [status, setStatus] = useState<CopilotStatus | null>(null);
  const [convs, setConvs] = useState<CopilotConversation[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [messages, setMessages] = useState<CopilotMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const polls = useRef(0);

  const loadConvs = useCallback(async () => {
    try {
      setConvs(await api<CopilotConversation[]>(`${base}/conversations`));
    } catch {
      setConvs([]);
    }
  }, [base]);

  const loadConv = useCallback(async (id: string) => {
    try {
      const d = await api<CopilotConversationDetail>(`${base}/conversations/${id}`);
      setMessages(d.messages);
      return d.messages;
    } catch (e) {
      setError(toApiError(e));
      return [];
    }
  }, [base]);

  useEffect(() => {
    if (!canUse) return;
    api<CopilotStatus>(`${base}/status`).then(setStatus).catch(() => setStatus(null));
    void loadConvs();
  }, [canUse, base, loadConvs]);

  const pending = messages.some((m) => m.status === "PENDING" || m.status === "RUNNING");
  useEffect(() => {
    if (!pending || !current) return;
    polls.current = 0;
    const id = setInterval(async () => {
      polls.current += 1;
      const msgs = await loadConv(current);
      if (!msgs.some((m) => m.status === "PENDING" || m.status === "RUNNING") || polls.current > POLL_LIMIT) {
        clearInterval(id);
        void loadConvs();
      }
    }, POLL_MS);
    return () => clearInterval(id);
  }, [pending, current, loadConv, loadConvs]);

  async function send() {
    const text = question.trim();
    if (!text || sending) return;
    setSending(true);
    setError(null);
    try {
      const r = await api<{ conversation_id: string; message: CopilotMessage }>(`${base}/ask`, {
        method: "POST", body: { question: text, conversation_id: current, locale },
      });
      setQuestion("");
      setCurrent(r.conversation_id);
      await loadConv(r.conversation_id);
      void loadConvs();
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setSending(false);
    }
  }

  function open(id: string | null) {
    setCurrent(id);
    setError(null);
    setMessages([]);
    if (id) void loadConv(id);
  }

  if (session.status !== "ready") return <Skeleton />;
  if (!canUse) return <div className="card"><h1>{t.catalog.noAccessTitle}</h1><p className="muted">{t.copilot.noAccessBody}</p></div>;

  const ready = status?.configured ?? false;
  const toolLabel = (n: string) => t.copilot.tools[n] ?? n;
  const argsText = (a?: Record<string, unknown>) => Object.entries(a ?? {}).filter(([, v]) => v !== null && v !== "" && v !== false).map(([k, v]) => `${k}=${String(v)}`).join(", ");

  return (
    <div className="stack">
      <div className="page-head"><h1>{t.copilot.title}</h1></div>
      <p className="hint">{t.copilot.note}</p>
      {status && !ready ? <Banner>{t.copilot.notConfigured}</Banner> : null}
      {status && ready ? (
        <p className="hint" data-testid="copilot-status">
          {status.mock ? <strong>{t.copilot.mockBadge}</strong> : fill(t.copilot.configured, { provider: status.provider ?? "", model: status.model ?? "" })}
        </p>
      ) : null}
      {error ? <Banner>{errorText(t, error)}</Banner> : null}
      <div className="toolbar">
        <label className="sr-only" htmlFor="cp-conv">{t.copilot.history}</label>
        <select id="cp-conv" value={current ?? ""} onChange={(e) => open(e.target.value || null)}>
          <option value="">{t.copilot.newChat}</option>
          {convs.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
        </select>
      </div>
      <section className="card" aria-live="polite" data-testid="copilot-messages">
        {messages.length === 0 ? <p className="muted">{t.copilot.empty}</p> : messages.map((m) => (
          <div key={m.id} className={m.role === "user" ? "chat-user" : "chat-assistant"} style={{ marginBottom: 12 }}>
            <strong>{m.role === "user" ? t.copilot.you : t.copilot.assistant}</strong>
            {m.status === "PENDING" || m.status === "RUNNING" ? <p className="muted">{t.copilot.thinking}</p> : null}
            {m.status === "FAILED" ? <div className="issue issue-warning">{t.copilot.failures[m.error_code ?? ""] ?? t.copilot.failureDefault}</div> : null}
            {m.content ? <p style={{ whiteSpace: "pre-wrap" }}>{m.content}</p> : null}
            {m.role === "assistant" && m.status === "DONE" && m.tool_trace.length > 0 ? (
              <details>
                <summary>{t.copilot.sources} ({m.tool_trace.length})</summary>
                <p className="hint">{t.copilot.sourcesNote}</p>
                <ul className="plain">
                  {m.tool_trace.map((e, i) => (
                    <li key={i}>
                      {toolLabel(e.tool)}{argsText(e.args) ? ` — ${argsText(e.args)}` : ""}
                      {e.rows != null ? ` · ${fill(t.copilot.rows, { n: e.rows })}` : ""}
                      {e.truncated ? ` · ${t.copilot.truncated}` : ""}
                      {e.error ? ` · ${t.copilot.toolErrors[e.error] ?? e.error}` : ""}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </div>
        ))}
      </section>
      <form onSubmit={(e) => { e.preventDefault(); void send(); }} className="stack">
        <textarea
          aria-label={t.copilot.placeholder} placeholder={t.copilot.placeholder} value={question} maxLength={MAX_LEN} rows={3}
          disabled={!ready} onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void send(); }}
        />
        <div className="toolbar">
          <Button type="submit" loading={sending} disabled={!ready || pending || question.trim() === ""}>{t.copilot.send}</Button>
          <span className="hint">{fill(t.copilot.charsLeft, { n: MAX_LEN - question.length })}</span>
        </div>
      </form>
    </div>
  );
}
