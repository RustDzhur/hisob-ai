import { expect, test } from "@playwright/test";
import { BASE_URL } from "./helpers";

const uniq = () => `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;

// Контексты без cookie языка: проверяем поведение «как у нового пользователя».
test("по умолчанию интерфейс на узбекском", async ({ browser }) => {
  const ctx = await browser.newContext({ baseURL: BASE_URL, storageState: { cookies: [], origins: [] } });
  const page = await ctx.newPage();
  await page.goto("/login");
  await expect(page.locator("html")).toHaveAttribute("lang", "uz");
  await expect(page.getByRole("heading", { name: "Hisob AI ga kirish" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Kirish" })).toBeVisible();

  // ошибки сервера переводятся по коду
  await page.getByLabel("Email").fill("nobody@example.uz");
  await page.getByLabel("Parol").fill("some-password-1");
  await page.getByRole("button", { name: "Kirish" }).click();
  await expect(page.locator("form").getByRole("alert")).toHaveText("Email yoki parol notoʻgʻri.");
  await ctx.close();
});

test("переключение языка сохраняется после перезагрузки и на других страницах", async ({ browser }) => {
  const ctx = await browser.newContext({ baseURL: BASE_URL, storageState: { cookies: [], origins: [] } });
  const page = await ctx.newPage();
  await page.goto("/login");
  await page.getByLabel("Til").selectOption("en");
  await expect(page.getByRole("heading", { name: "Sign in to Hisob AI" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");

  await page.reload();
  await expect(page.getByRole("heading", { name: "Sign in to Hisob AI" })).toBeVisible();
  await page.getByRole("link", { name: "Sign up" }).click();
  await expect(page.getByRole("heading", { name: "Create an account" })).toBeVisible();

  await page.getByLabel("Language").selectOption("ru");
  await expect(page.getByRole("heading", { name: "Создание аккаунта" })).toBeVisible();
  await ctx.close();
});

test("весь путь на узбекском: регистрация, организация, роль; затем смена языка внутри приложения", async ({
  browser,
}) => {
  const ctx = await browser.newContext({ baseURL: BASE_URL, storageState: { cookies: [], origins: [] } });
  const page = await ctx.newPage();
  await page.goto("/register");
  await page.getByLabel("Ism va familiya").fill("Aliyev Vali");
  await page.getByLabel("Email").fill(`uz-${uniq()}@example.uz`);
  await page.getByLabel("Parol").fill("correct horse battery");
  await page.getByRole("button", { name: "Roʻyxatdan oʻtish" }).click();

  await expect(page.getByRole("heading", { name: "Tashkilot yarating" })).toBeVisible();
  await page.getByLabel("Tashkilot nomi").fill(`Mening kompaniyam ${uniq()}`);
  await page.getByRole("button", { name: "Tashkilot yaratish" }).click();

  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  await expect(page.getByText("Egasi", { exact: true })).toBeVisible(); // роль «Владелец»
  await expect(page.getByRole("link", { name: "Ishtirokchilar" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Audit jurnali" })).toBeVisible();
  await expect(page.getByText("UZS")).toBeVisible(); // базовая валюта по умолчанию — сум

  // язык в верхней панели: русский, затем английский; данные не теряются
  await page.getByLabel("Til").selectOption("ru");
  await expect(page.getByText("Владелец", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Журнал аудита" }).click();
  await expect(page.getByRole("heading", { name: "Журнал аудита" })).toBeVisible();
  await expect(page.getByRole("table").getByText("Создание организации")).toBeVisible();
  await page.getByLabel("Язык").selectOption("en");
  await expect(page.getByRole("table").getByText("Organization created")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Audit log" })).toBeVisible();
  await ctx.close();
});
