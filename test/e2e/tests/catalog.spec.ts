import { expect, test, type Browser, type Page } from "@playwright/test";
import { newContext } from "./helpers";

const PASSWORD = "correct horse battery";
const uniq = () => `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;

async function register(page: Page, email: string, name = "Тест Тестов") {
  await page.getByLabel("Имя и фамилия").fill(name);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Зарегистрироваться" }).click();
}

async function ownerWithOrg(browser: Browser) {
  const ctx = await newContext(browser);
  const page = await ctx.newPage();
  await page.goto("/register");
  await register(page, `owner-${uniq()}@example.uz`, "Владелец Иванов");
  await page.getByLabel("Название организации").fill(`ООО Справочники ${uniq()}`);
  await page.getByRole("button", { name: "Создать организацию" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  return { ctx, page, orgUrl: page.url() };
}

test("валюты: базовая закреплена, включение и отключение, названия на языке интерфейса", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.getByRole("link", { name: "Валюты" }).click();
  await expect(o.page.getByRole("heading", { name: "Валюты" })).toBeVisible();

  const uzs = o.page.getByRole("row", { name: /UZS/ });
  await expect(uzs.getByText("Базовая")).toBeVisible();
  await expect(uzs.getByRole("button")).toHaveCount(0); // базовую отключить нельзя
  await expect(o.page.getByRole("row", { name: /USD/ }).getByText("Выключена")).toBeVisible();

  await o.page.getByRole("button", { name: "Включить: USD" }).click();
  await expect(o.page.getByRole("row", { name: /USD/ }).getByText("Включена")).toBeVisible();

  await o.page.getByRole("button", { name: "Отключить: USD" }).click();
  await o.page.getByRole("button", { name: "Подтвердить" }).click();
  await expect(o.page.getByRole("row", { name: /USD/ }).getByText("Выключена")).toBeVisible();

  await expect(o.page.getByText("Узбекский сум")).toBeVisible();
  await o.page.getByLabel("Язык").selectOption("en");
  await expect(o.page.getByText("Uzbekistani som")).toBeVisible();
  await o.page.getByLabel("Language").selectOption("uz");
  await expect(o.page.getByText("Oʻzbekiston soʻmi")).toBeVisible();
  await o.ctx.close();
});

test("единицы измерения: системные, своя единица, ошибки, правка и отключение", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.getByRole("link", { name: "Единицы измерения" }).click();
  const table = o.page.getByRole("table");
  await expect(table.getByRole("row", { name: /pcs/ })).toContainText("шт");
  await expect(table.getByRole("row", { name: /kg/ })).toContainText("Системная");

  await o.page.getByRole("button", { name: "Добавить единицу" }).click();
  // без названия
  await o.page.getByLabel("Код", { exact: true }).fill("box");
  await o.page.getByRole("button", { name: "Создать" }).click();
  await expect(o.page.getByText("Нужно название хотя бы на одном языке.").first()).toBeVisible();
  // занятый код (системный, регистр не важен)
  await o.page.getByLabel("Код", { exact: true }).fill("PCS");
  await o.page.getByLabel("Название (русский)").fill("Штуки");
  await o.page.getByRole("button", { name: "Создать" }).click();
  await expect(o.page.getByText("Код уже используется.")).toBeVisible();
  // успех
  await o.page.getByLabel("Код", { exact: true }).fill("box");
  await o.page.getByRole("button", { name: "Создать" }).click();
  const box = table.getByRole("row", { name: /box/ });
  await expect(box).toContainText("Штуки");
  await expect(box).toContainText("Своя");

  // правка названий
  await o.page.getByRole("button", { name: "Изменить названия: box" }).click();
  await o.page.locator("input[id^='e-'][id$='-ru']").fill("Коробка");
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(box).toContainText("Коробка");

  // отключение
  await o.page.getByRole("button", { name: "Отключить: box" }).click();
  await expect(box.getByText("Отключена")).toBeVisible();
  await o.page.getByRole("button", { name: "Включить: box" }).click();
  await expect(box.getByText("Активна")).toBeVisible();

  // у системной единицы нет кнопок управления
  await expect(table.getByRole("row", { name: /pcs/ }).getByRole("button")).toHaveCount(0);
  // события попали в журнал аудита
  await o.page.getByRole("link", { name: "Журнал аудита" }).click();
  await expect(o.page.getByRole("table").getByText("Создание единицы измерения")).toBeVisible();
  await o.ctx.close();
});

test("кладовщик читает справочники, но не управляет ими", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  const email = `store-${uniq()}@example.uz`;
  await o.page.goto(`${o.orgUrl}/members`);
  await o.page.getByRole("button", { name: "Пригласить" }).click();
  await o.page.getByLabel("Email приглашаемого").fill(email);
  await o.page.getByLabel("Роль", { exact: true }).selectOption({ label: "Кладовщик" });
  await o.page.getByRole("button", { name: "Создать приглашение" }).click();
  const link = await o.page.getByLabel("Ссылка-приглашение").inputValue();

  const sctx = await newContext(browser);
  const s = await sctx.newPage();
  await s.goto(link);
  await s.getByRole("link", { name: "Зарегистрироваться" }).click();
  await register(s, email, "Кладовщик Сидоров");
  await s.getByRole("button", { name: "Принять приглашение" }).click();
  await expect(s).toHaveURL(/\/o\/[0-9a-f-]{36}$/);

  await s.getByRole("link", { name: "Единицы измерения" }).click();
  await expect(s.getByRole("table").getByRole("row", { name: /pcs/ })).toBeVisible();
  await expect(s.getByRole("button", { name: "Добавить единицу" })).toHaveCount(0);
  await s.getByRole("link", { name: "Валюты" }).click();
  await expect(s.getByRole("row", { name: /UZS/ }).getByText("Базовая")).toBeVisible();
  await expect(s.getByRole("button", { name: /Включить|Отключить/ })).toHaveCount(0);

  await sctx.close();
  await o.ctx.close();
});
