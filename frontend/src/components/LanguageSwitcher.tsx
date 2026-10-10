"use client";

import { useLocale, useT } from "@/i18n/LocaleProvider";
import { isLocale, LOCALES } from "@/i18n";

export function LanguageSwitcher() {
  const t = useT();
  const { locale, setLocale } = useLocale();
  return (
    <select
      className="lang-switch"
      aria-label={t.lang.label}
      value={locale}
      onChange={(e) => isLocale(e.target.value) && setLocale(e.target.value)}
    >
      {LOCALES.map((l) => (
        <option key={l} value={l}>
          {t.lang[l]}
        </option>
      ))}
    </select>
  );
}
