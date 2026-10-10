import { expect, test, type Browser, type Page } from "@playwright/test";
import { newContext } from "./helpers";

const PASSWORD = "correct horse battery";
const uniq = () => `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;

async function register(page: Page, email: string, name: string) {
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
  await page.getByLabel("Название организации").fill(`ООО Склады ${uniq()}`);
  await page.getByRole("button", { name: "Создать организацию" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  return { ctx, page, orgUrl: page.url() };
}

/** Приглашает пользователя с ролью и возвращает его страницу уже внутри организации. */
async function memberWithRole(browser: Browser, owner: { page: Page; orgUrl: string }, role: string, name: string) {
  const email = `m-${uniq()}@example.uz`;
  await owner.page.goto(`${owner.orgUrl}/members`);
  await owner.page.getByRole("button", { name: "Пригласить" }).click();
  await owner.page.getByLabel("Email приглашаемого").fill(email);
  await owner.page.getByLabel("Роль", { exact: true }).selectOption({ label: role });
  await owner.page.getByRole("button", { name: "Создать приглашение" }).click();
  const link = await owner.page.getByLabel("Ссылка-приглашение").inputValue();
  const ctx = await newContext(browser);
  const page = await ctx.newPage();
  await page.goto(link);
  await page.getByRole("link", { name: "Зарегистрироваться" }).click();
  await register(page, email, name);
  await page.getByRole("button", { name: "Принять приглашение" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  return { ctx, page };
}

test("склады: создание, дубликат названия, правка, архив, поиск", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.getByRole("link", { name: "Склады" }).click();
  await expect(o.page.getByText("Складов пока нет.")).toBeVisible();

  await o.page.getByRole("button", { name: "Добавить склад" }).first().click();
  await o.page.getByLabel("Название", { exact: true }).fill("Центральный");
  await o.page.getByLabel("Адрес").fill("Ташкент, Чиланзар");
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  const table = o.page.getByRole("table");
  await expect(table.getByRole("row", { name: /Центральный/ })).toContainText("Ташкент, Чиланзар");

  await o.page.getByRole("button", { name: "Добавить склад" }).click();
  await o.page.getByLabel("Название", { exact: true }).fill("ЦЕНТРАЛЬНЫЙ");
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(o.page.getByText("Название уже используется.")).toBeVisible();
  await o.page.getByLabel("Название", { exact: true }).fill("Северный");
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(table.getByRole("row", { name: /Северный/ })).toBeVisible();

  await o.page.getByRole("button", { name: "Изменить: Северный" }).click();
  await o.page.getByLabel("Адрес").fill("Самарканд");
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(table.getByRole("row", { name: /Северный/ })).toContainText("Самарканд");

  await o.page.getByRole("searchbox", { name: "Поиск" }).fill("самарканд");
  await expect(o.page.getByRole("table").locator("tbody tr")).toHaveCount(1);
  await o.page.getByRole("button", { name: "Сбросить" }).click();

  await o.page.getByRole("button", { name: "В архив: Северный" }).click();
  await o.page.getByRole("button", { name: "Подтвердить" }).click();
  await expect(o.page.getByRole("table").getByText("Северный")).toHaveCount(0);
  await o.page.getByLabel("Статус", { exact: true }).selectOption("archived");
  await expect(o.page.getByRole("table").getByRole("row", { name: /Северный/ })).toContainText("В архиве");
  await o.page.getByRole("button", { name: "Вернуть: Северный" }).click();
  await o.page.getByLabel("Статус", { exact: true }).selectOption("active");
  await expect(o.page.getByRole("table").getByRole("row", { name: /Северный/ })).toBeVisible();
  await o.ctx.close();
});

test("права на склады: кладовщик управляет складами, но не товарами; бухгалтер только смотрит", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.goto(`${o.orgUrl}/warehouses`);
  await o.page.getByRole("button", { name: "Добавить склад" }).first().click();
  await o.page.getByLabel("Название", { exact: true }).fill("Общий");
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(o.page.getByRole("table").getByRole("row", { name: /Общий/ })).toBeVisible();

  const store = await memberWithRole(browser, o, "Кладовщик", "Кладовщик Сидоров");
  await store.page.getByRole("link", { name: "Склады" }).click();
  await expect(store.page.getByRole("button", { name: "Добавить склад" })).toBeVisible();
  await expect(store.page.getByRole("button", { name: "Изменить: Общий" })).toBeVisible();
  await store.page.getByRole("link", { name: "Товары и услуги" }).click();
  await expect(store.page.getByRole("button", { name: "Добавить товар или услугу" })).toHaveCount(0);

  const acc = await memberWithRole(browser, o, "Бухгалтер", "Бухгалтер Петрова");
  await acc.page.getByRole("link", { name: "Склады" }).click();
  await expect(acc.page.getByRole("table").getByRole("row", { name: /Общий/ })).toBeVisible();
  await expect(acc.page.getByRole("button", { name: "Добавить склад" })).toHaveCount(0);
  await expect(acc.page.getByRole("button", { name: /Изменить|В архив/ })).toHaveCount(0);

  await store.ctx.close();
  await acc.ctx.close();
  await o.ctx.close();
});

test("склады на узбекском", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.goto(`${o.orgUrl}/warehouses`);
  await o.page.getByLabel("Язык").selectOption("uz");
  await expect(o.page.getByRole("heading", { name: "Omborlar" })).toBeVisible();
  await expect(o.page.getByText("Hozircha omborlar yoʻq.")).toBeVisible();
  await o.ctx.close();
});
