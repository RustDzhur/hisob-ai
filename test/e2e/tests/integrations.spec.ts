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
  await page.getByLabel("Название организации").fill(`ООО Инт ${uniq()}`);
  await page.getByRole("button", { name: "Создать организацию" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  return { ctx, page, orgUrl: page.url() };
}
type Org = Awaited<ReturnType<typeof ownerWithOrg>>;

async function memberWithRole(browser: Browser, owner: Org, role: string, name: string) {
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

test("интеграции: честные статусы, тестовая синхронизация и журнал", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.getByRole("link", { name: "Интеграции", exact: true }).click();
  for (const code of ["multibank", "faktura_uz", "didox"]) {
    const card = o.page.getByTestId(`provider-${code}`);
    await expect(card).toContainText("Не настроена");
    await expect(card).toContainText("Официальная документация и тестовые доступы не получены");
    await expect(card.getByRole("button")).toHaveCount(0); // подключать нечего — кнопок нет
  }
  await expect(o.page.getByTestId("provider-generic_file")).toContainText("Работает");
  const mock = o.page.getByTestId("provider-mock");
  await expect(mock).toContainText("Тестовый режим");
  await expect(mock).toContainText("Данные ненастоящие");
  await expect(o.page.getByText("Запусков пока не было.")).toBeVisible();

  await mock.getByRole("button", { name: "Включить" }).click();
  await mock.getByRole("button", { name: "Запустить тестовую синхронизацию" }).click();
  await expect(o.page.getByTestId("runs")).toContainText("MOCK");
  await expect(o.page.getByTestId("runs")).toContainText("Успешно");
  await expect(mock).toContainText("Курсор: 3");
  await mock.getByRole("button", { name: "Запустить тестовую синхронизацию" }).click();
  await expect(mock).toContainText("Курсор: 6");
  await mock.getByRole("button", { name: "Выключить" }).click();
  await expect(mock.getByRole("button", { name: "Включить" })).toBeVisible();

  // аудитор читает, но не управляет; кладовщик раздела не видит
  const au = await memberWithRole(browser, o, "Аудитор (только чтение)", "Аудитор Петров");
  await au.page.goto(`${o.orgUrl}/integrations`);
  await expect(au.page.getByTestId("provider-mock")).toContainText("Тестовый режим");
  await expect(au.page.getByTestId("provider-mock").getByRole("button")).toHaveCount(0);
  const keeper = await memberWithRole(browser, o, "Кладовщик", "Кладовщик Алиев");
  await keeper.page.goto(`${o.orgUrl}/integrations`);
  await expect(keeper.page.getByText("У вас нет права просматривать интеграции.")).toBeVisible();
  await expect(keeper.page.getByRole("link", { name: "Интеграции", exact: true })).toHaveCount(0);
  await au.ctx.close();
  await keeper.ctx.close();
  await o.ctx.close();
});

test("интеграции на узбекском и английском", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.goto(`${o.orgUrl}/integrations`);
  await o.page.getByLabel("Язык").selectOption("uz");
  await expect(o.page.getByRole("heading", { name: "Integratsiyalar", exact: true })).toBeVisible();
  await expect(o.page.getByTestId("provider-multibank")).toContainText("Sozlanmagan");
  await o.page.getByLabel("Til").selectOption("en");
  await expect(o.page.getByRole("heading", { name: "Integrations", exact: true })).toBeVisible();
  await expect(o.page.getByTestId("provider-multibank")).toContainText("Not configured");
  await o.ctx.close();
});
