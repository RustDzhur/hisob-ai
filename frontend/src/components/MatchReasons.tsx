"use client";

import { fill } from "@/i18n/errors";
import { useT } from "@/i18n/LocaleProvider";
import type { MatchReason } from "@/lib/api";

/** Причины предложения/сопоставления: коды с сервера -> текст на языке интерфейса. */
export function MatchReasons({ reasons }: { reasons: MatchReason[] }) {
  const t = useT();
  if (!reasons.length) return null;
  return (
    <ul className="hint" style={{ margin: "4px 0", paddingLeft: 18 }}>
      {reasons.map((r, i) => <li key={i}>{fill(t.bank.reasonCodes[r.code] ?? r.code, r.params)}</li>)}
    </ul>
  );
}
