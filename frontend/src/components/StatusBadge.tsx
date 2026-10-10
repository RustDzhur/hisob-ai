"use client";

import { useT } from "@/i18n/LocaleProvider";

const CLASS: Record<string, string> = {
  NEEDS_REVIEW: "badge-warn",
  APPROVED: "badge-ok",
  POSTED: "badge-ok",
  RECONCILED: "badge-ok",
  REJECTED: "badge-danger",
  FAILED: "badge-danger",
  DUPLICATE: "badge-info",
  ARCHIVED: "badge-muted",
  CANCELLED: "badge-muted",
};

/** Подпись и цвет статуса документа; промежуточные статусы обработки показываются одним «Обработка». */
export function StatusBadge({ status }: { status: string }) {
  const t = useT();
  return (
    <span className={`badge ${CLASS[status] ?? "badge-info"}`} title={status}>
      {t.documents.status[status] ?? status}
    </span>
  );
}
