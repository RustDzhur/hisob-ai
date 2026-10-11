import { createHmac } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import { newContext } from "./helpers";

const PASSWORD = "correct horse battery";
const uniq = () => `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;

// TOTP (RFC 6238, SHA-1, 6 цифр, 30 с): тот же алгоритм, что на сервере; step — смещение интервала от текущего
function totp(secretB32: string, stepOffset = 0): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secretB32.replace(/=+$/, "")) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g)!.map((b) => parseInt(b, 2)));
  const counter = Math.floor(Date.now() / 1000 / 30) + stepOffset;
  const buf = Buffer.alloc(8);
  buf.writeBigUInt64BE(BigInt(counter));
  const mac = createHmac("sha1", key).update(buf).digest();
  const off = mac[mac.length - 1] & 0x0f;
  const code = (mac.readUInt32BE(off) & 0x7fffffff) % 1_000_000;
  return String(code).padStart(6, "0");
}

async function register(page: Page, email: string) {
  await page.goto("/register");
  await page.getByLabel("Имя и фамилия").fill("Тест Второй Фактор");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Зарегистрироваться" }).click();
  await expect(page).toHaveURL(/\/onboarding|\/o\//);
}

test("второй фактор: включение, вход с кодом, неверный код, код восстановления, выключение", async ({ browser }) => {
  const email = `mfa-${uniq()}@example.uz`;
  const ctx = await newContext(browser);
  const page = await ctx.newPage();
  await register(page, email);
  await page.getByLabel("Название организации").fill(`ООО МФА ${uniq()}`);
  await page.getByRole("button", { name: "Создать организацию" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  const orgUrl = page.url();

  await page.goto(`${orgUrl}/account`);
  const mfa = page.getByTestId("mfa");
  await expect(mfa.getByText("Выключена")).toBeVisible();
  await mfa.getByRole("button", { name: "Включить" }).click();
  const secret = (await mfa.getByTestId("mfa-secret").innerText()).trim();
  await mfa.getByLabel("Код из приложения").fill("000000");
  await mfa.getByRole("button", { name: "Подтвердить и включить" }).click();
  await expect(mfa.getByText("Неверный код.")).toBeVisible();
  await mfa.getByLabel("Код из приложения").fill(totp(secret));
  await mfa.getByRole("button", { name: "Подтвердить и включить" }).click();
  await expect(mfa.getByTestId("mfa-codes")).toBeVisible();
  const codes = (await mfa.getByTestId("mfa-codes").locator("li").allInnerTexts()).map((s) => s.trim());
  expect(codes).toHaveLength(8);
  await mfa.getByRole("button", { name: "Я сохранил коды" }).click();
  await expect(mfa.getByText("Включена")).toBeVisible();
  await expect(mfa.getByText("Осталось кодов восстановления: 8")).toBeVisible();

  // выход и вход: сначала пароль, затем запрос кода
  const login = async (p: Page, code: string | null) => {
    await p.goto("/login");
    await p.getByLabel("Email").fill(email);
    await p.getByLabel("Пароль").fill(PASSWORD);
    await p.getByRole("button", { name: "Войти" }).click();
    await expect(p.getByText("Для этого аккаунта включён второй фактор.")).toBeVisible();
    if (code !== null) {
      await p.getByLabel("Код из приложения-аутентификатора или код восстановления").fill(code);
      await p.getByRole("button", { name: "Войти" }).click();
    }
  };
  const ctx2 = await newContext(browser);
  const p2 = await ctx2.newPage();
  await login(p2, "111111");
  await expect(p2.getByText("Неверный код.")).toBeVisible();
  await p2.getByLabel("Код из приложения-аутентификатора или код восстановления").fill(totp(secret, 1)); // следующий интервал: прежний уже использован
  await p2.getByRole("button", { name: "Войти" }).click();
  await expect(p2).toHaveURL(/\/o\/[0-9a-f-]{36}/);

  // код восстановления работает один раз
  const ctx3 = await newContext(browser);
  const p3 = await ctx3.newPage();
  await login(p3, codes[0]);
  await expect(p3).toHaveURL(/\/o\/[0-9a-f-]{36}/);
  const ctx4 = await newContext(browser);
  const p4 = await ctx4.newPage();
  await login(p4, codes[0]);
  await expect(p4.getByText("Неверный код.")).toBeVisible();

  // выключение: пароль и код
  await p3.goto(`${orgUrl}/account`);
  const m3 = p3.getByTestId("mfa");
  await expect(m3.getByText("Осталось кодов восстановления: 7")).toBeVisible();
  await m3.getByLabel("Пароль").fill(PASSWORD);
  await m3.getByLabel("Код из приложения").fill(codes[1]);
  await m3.getByRole("button", { name: "Выключить второй фактор" }).click();
  await expect(m3.getByText("Выключена")).toBeVisible();
  for (const c of [ctx, ctx2, ctx3, ctx4]) await c.close();
});

test("второй фактор на узбекском и английском", async ({ browser }) => {
  const ctx = await newContext(browser);
  const page = await ctx.newPage();
  await register(page, `mfa-${uniq()}@example.uz`);
  await page.getByLabel("Название организации").fill(`ООО МФА ${uniq()}`);
  await page.getByRole("button", { name: "Создать организацию" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  await page.goto(`${page.url()}/account`);
  await page.getByLabel("Язык").selectOption("uz");
  await expect(page.getByRole("heading", { name: "Ikki bosqichli himoya" })).toBeVisible();
  await page.getByLabel("Til").selectOption("en");
  await expect(page.getByRole("heading", { name: "Two-factor authentication" })).toBeVisible();
  await ctx.close();
});
