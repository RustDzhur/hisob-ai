import type { Browser, BrowserContext } from "@playwright/test";

export const BASE_URL = process.env.BASE_URL ?? "http://localhost:3300";

/** Старые сценарии написаны на русском: язык задаётся cookie (по умолчанию в продукте — узбекский). */
export const ruState = {
  cookies: [
    {
      name: "hisob_lang",
      value: "ru",
      domain: new URL(BASE_URL).hostname,
      path: "/",
      expires: -1,
      httpOnly: false,
      secure: false,
      sameSite: "Lax" as const,
    },
  ],
  origins: [],
};

/** Новый «браузер» с русским интерфейсом (browser.newContext() не наследует настройки проекта). */
export function newContext(browser: Browser): Promise<BrowserContext> {
  return browser.newContext({ baseURL: BASE_URL, storageState: ruState });
}
