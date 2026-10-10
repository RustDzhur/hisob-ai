import { newContext } from "./helpers";
import { expect, test, type Browser, type Page } from "@playwright/test";

const PASSWORD = "correct horse battery";
const NEW_PASSWORD = "another strong pass 42";
const uniq = () => `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;

async function register(page: Page, email: string, name = "Тест Тестов") {
  await page.getByLabel("Имя и фамилия").fill(name);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Зарегистрироваться" }).click();
}

async function login(page: Page, email: string, password = PASSWORD) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Пароль").fill(password);
  await page.getByRole("button", { name: "Войти" }).click();
}

async function ownerWithOrg(browser: Browser) {
  const ctx = await newContext(browser);
  const page = await ctx.newPage();
  const email = `owner-${uniq()}@example.uz`;
  await page.goto("/register");
  await register(page, email, "Владелец Иванов");
  await page.getByLabel("Название организации").fill(`ООО Аудит ${uniq()}`);
  await page.getByRole("button", { name: "Создать организацию" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  return { ctx, page, email, orgUrl: page.url() };
}

test("журнал аудита: события, фильтр, доступ только по праву", async ({ browser }) => {
  const owner = await ownerWithOrg(browser);
  const accEmail = `acc-${uniq()}@example.uz`;

  // приглашаем бухгалтера -> событие в журнале
  await owner.page.goto(`${owner.orgUrl}/members`);
  await owner.page.getByRole("button", { name: "Пригласить" }).click();
  await owner.page.getByLabel("Email приглашаемого").fill(accEmail);
  await owner.page.getByRole("button", { name: "Создать приглашение" }).click();
  const link = await owner.page.getByLabel("Ссылка-приглашение").inputValue();

  await owner.page.getByRole("link", { name: "Журнал аудита" }).click();
  await expect(owner.page.getByRole("heading", { name: "Журнал аудита" })).toBeVisible();
  const table = owner.page.getByRole("table");
  await expect(table.getByText("Приглашение участника")).toBeVisible();
  await expect(table.getByText("Создание организации")).toBeVisible();
  await expect(table.getByText("Владелец Иванов").first()).toBeVisible();

  // подробности события
  await table.getByText("Подробности").first().click();
  await expect(owner.page.locator("pre.json").first()).toContainText(accEmail);

  // фильтр по действию
  await owner.page.getByLabel("Действие").selectOption("org.created");
  await expect(table.getByText("Создание организации")).toBeVisible();
  await expect(table.getByText("Приглашение участника")).toHaveCount(0);
  await owner.page.getByRole("button", { name: "Сбросить" }).click();
  await expect(table.getByText("Приглашение участника")).toBeVisible();

  // бухгалтер: пункта меню нет, прямой переход -> «Нет доступа»
  const mctx = await newContext(browser);
  const m = await mctx.newPage();
  await m.goto(link);
  await m.getByRole("link", { name: "Зарегистрироваться" }).click();
  await register(m, accEmail, "Бухгалтер Петрова");
  await m.getByRole("button", { name: "Принять приглашение" }).click();
  await expect(m).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  await expect(m.getByRole("link", { name: "Журнал аудита" })).toHaveCount(0);
  await m.goto(`${owner.orgUrl}/audit`);
  await expect(m.getByRole("heading", { name: "Нет доступа" })).toBeVisible();

  await mctx.close();
  await owner.ctx.close();
});

test("смена пароля: старый перестаёт работать, другие сессии завершены", async ({ browser }) => {
  const owner = await ownerWithOrg(browser);
  const other = await newContext(browser);
  const o = await other.newPage();
  await login(o, owner.email);
  await expect(o.getByRole("heading", { name: /Организация:/ })).toBeVisible();

  await owner.page.getByRole("link", { name: "Безопасность" }).click();
  await owner.page.getByLabel("Текущий пароль").fill("wrong-password-1");
  await owner.page.getByLabel("Новый пароль", { exact: true }).fill(NEW_PASSWORD);
  await owner.page.getByLabel("Повторите новый пароль").fill(NEW_PASSWORD);
  await owner.page.getByRole("button", { name: "Сменить пароль" }).click();
  await expect(owner.page.getByText("Текущий пароль указан неверно.").or(owner.page.getByText("Неверный пароль."))).toBeVisible();

  await owner.page.getByLabel("Текущий пароль").fill(PASSWORD);
  await owner.page.getByRole("button", { name: "Сменить пароль" }).click();
  await expect(owner.page.getByText("Пароль изменён. Остальные сессии завершены.")).toBeVisible();

  await o.reload(); // сессия в другом браузере завершена
  await expect(o).toHaveURL(/\/login$/);
  await login(o, owner.email, PASSWORD);
  await expect(o.getByRole("alert").filter({ hasText: "Неверный email или пароль." })).toBeVisible();
  await login(o, owner.email, NEW_PASSWORD);
  await expect(o.getByRole("heading", { name: /Организация:/ })).toBeVisible();

  await other.close();
  await owner.ctx.close();
});

test("сессии: завершение чужой сессии и «выйти на всех устройствах»", async ({ browser }) => {
  const owner = await ownerWithOrg(browser);
  const other = await newContext(browser);
  const o = await other.newPage();
  await login(o, owner.email);
  await expect(o.getByRole("heading", { name: /Организация:/ })).toBeVisible();

  await owner.page.goto(`${owner.orgUrl}/account`);
  const rows = owner.page.getByRole("table").getByRole("row");
  await expect(rows).toHaveCount(2);
  await expect(owner.page.getByText("Эта сессия")).toBeVisible();

  // завершаем не текущую
  await rows.filter({ hasNot: owner.page.getByText("Эта сессия") }).getByRole("button", { name: /Завершить/ }).click();
  await expect(rows).toHaveCount(1);
  await o.reload();
  await expect(o).toHaveURL(/\/login$/);

  // выйти везде: текущая тоже закрывается
  await owner.page.getByRole("button", { name: "Выйти на всех устройствах" }).click();
  await owner.page.getByRole("button", { name: "Подтвердить" }).click();
  await expect(owner.page).toHaveURL(/\/login$/);
  await owner.page.goto(owner.orgUrl);
  await expect(owner.page).toHaveURL(/\/login$/);

  await other.close();
  await owner.ctx.close();
});
