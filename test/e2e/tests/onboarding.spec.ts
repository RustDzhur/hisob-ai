import { expect, test, type Page } from "@playwright/test";

const PASSWORD = "correct horse battery";
const uniq = () => `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;

async function register(page: Page, email: string, name = "Тест Тестов") {
  await page.goto("/register");
  await page.getByLabel("Имя и фамилия").fill(name);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Зарегистрироваться" }).click();
}

async function createOrg(page: Page, name: string) {
  await expect(page.getByRole("heading", { name: "Создайте организацию" })).toBeVisible();
  await page.getByLabel("Название организации").fill(name);
  await page.getByRole("button", { name: "Создать организацию" }).click();
}

test("регистрация -> организация -> выход -> вход", async ({ page }) => {
  const email = `owner-${uniq()}@example.uz`;
  const org = `ООО Ромашка ${uniq()}`;

  await register(page, email);
  await createOrg(page, org);

  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { name: `Организация: ${org}` })).toBeVisible();
  await expect(page.getByText("Владелец")).toBeVisible();
  await expect(page.getByText("Asia/Tashkent")).toBeVisible();

  await page.getByRole("button", { name: "Выйти" }).click();
  await expect(page).toHaveURL(/\/login$/);

  // сессия действительно закрыта: защищённая страница возвращает на вход
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);

  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("heading", { name: `Организация: ${org}` })).toBeVisible();
});

test("неверный пароль: понятная ошибка, поля сохранены", async ({ page }) => {
  const email = `bad-${uniq()}@example.uz`;
  await register(page, email);
  await createOrg(page, `Org ${uniq()}`);
  await page.getByRole("button", { name: "Выйти" }).click();

  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Пароль").fill("wrong-password-1");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.locator("form").getByRole("alert")).toHaveText("Неверный email или пароль.");
  await expect(page.getByLabel("Email")).toHaveValue(email);
});

test("валидация формы регистрации и занятый email", async ({ page, browser }) => {
  const email = `dup-${uniq()}@example.uz`;
  await page.goto("/register");
  await page.getByRole("button", { name: "Зарегистрироваться" }).click();
  await expect(page.locator("form").getByRole("alert")).toHaveCount(3);

  await register(page, email);
  await expect(page.getByRole("heading", { name: "Создайте организацию" })).toBeVisible();

  const other = await browser.newContext();
  const p2 = await other.newPage();
  await register(p2, email, "Другой");
  await expect(p2.getByText("Email уже используется.")).toBeVisible();
  await other.close();
});

test("чужая организация недоступна, без входа — редирект на /login", async ({ page, browser }) => {
  await register(page, `a-${uniq()}@example.uz`);
  await createOrg(page, `Org A ${uniq()}`);
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  const foreignUrl = page.url();

  const ctx = await browser.newContext();
  const anon = await ctx.newPage();
  await anon.goto(foreignUrl);
  await expect(anon).toHaveURL(/\/login$/);

  await register(anon, `b-${uniq()}@example.uz`, "Другой Пользователь");
  await expect(anon.getByRole("heading", { name: "Создайте организацию" })).toBeVisible();
  await anon.goto(foreignUrl);
  await expect(anon.getByRole("heading", { name: "Организация не найдена" })).toBeVisible();
  await ctx.close();
});

test("повторная отправка формы организации не создаёт дубликат", async ({ page }) => {
  await register(page, `idem-${uniq()}@example.uz`);
  await expect(page.getByRole("heading", { name: "Создайте организацию" })).toBeVisible();
  await page.getByLabel("Название организации").fill(`Одна ${uniq()}`);
  const submit = page.getByRole("button", { name: "Создать организацию" });
  await submit.dblclick();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  const options = await page.locator("select option").allTextContents();
  expect(options.filter((t) => !t.includes("Создать организацию"))).toHaveLength(1);
});
