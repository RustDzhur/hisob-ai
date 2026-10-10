"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import { dictionaries, intlLocale, LANG_COOKIE, type Dict, type Locale } from "./index";

type Ctx = { locale: Locale; t: Dict; setLocale: (l: Locale) => void };
const LocaleContext = createContext<Ctx | null>(null);

export function LocaleProvider({ initial, children }: { initial: Locale; children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initial);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    document.documentElement.lang = l;
    // Язык — удобство конкретного браузера: cookie на год, без персональных данных.
    document.cookie = `${LANG_COOKIE}=${l}; path=/; max-age=31536000; samesite=lax`;
  }, []);

  const value = useMemo(() => ({ locale, t: dictionaries[locale], setLocale }), [locale, setLocale]);
  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

function useLocaleCtx(): Ctx {
  const ctx = useContext(LocaleContext);
  if (!ctx) throw new Error("LocaleProvider отсутствует");
  return ctx;
}

export const useT = (): Dict => useLocaleCtx().t;
export const useLocale = () => {
  const { locale, setLocale } = useLocaleCtx();
  return { locale, setLocale };
};

/** Форматирование дат по языку интерфейса; timeZone — часовой пояс организации (для событий). */
export function useFormat() {
  const { locale } = useLocaleCtx();
  return useMemo(() => {
    const tag = intlLocale[locale];
    return {
      // дата без времени (YYYY-MM-DD) показывается как календарный день, без сдвига часовых поясов
      dateOnly: (s: string) => {
        const [y, m, d] = s.split("-").map(Number);
        return new Intl.DateTimeFormat(tag, { dateStyle: "short" }).format(new Date(y, m - 1, d));
      },
      date: (iso: string) => new Intl.DateTimeFormat(tag, { dateStyle: "short" }).format(new Date(iso)),
      dateTime: (iso: string, timeZone?: string) =>
        new Intl.DateTimeFormat(tag, { dateStyle: "short", timeStyle: "medium", timeZone }).format(new Date(iso)),
    };
  }, [locale]);
}
