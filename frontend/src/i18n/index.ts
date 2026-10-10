import { en } from "./en";
import type { Dict } from "./ru";
import { ru } from "./ru";
import { uz } from "./uz";

export type Locale = "uz" | "ru" | "en";
export const LOCALES: Locale[] = ["uz", "ru", "en"];
export const DEFAULT_LOCALE: Locale = "uz"; // решение владельца: язык по умолчанию — узбекский
export const LANG_COOKIE = "hisob_lang";

export const dictionaries: Record<Locale, Dict> = { uz, ru, en };

// Регион для Intl: даты и числа форматируются по языку интерфейса.
export const intlLocale: Record<Locale, string> = { uz: "uz-UZ", ru: "ru-RU", en: "en-GB" };

export function isLocale(value: string | undefined | null): value is Locale {
  return value === "uz" || value === "ru" || value === "en";
}

export type { Dict };
