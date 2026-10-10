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
  await page.getByLabel("Название организации").fill(`ООО Бух ${uniq()}`);
  await page.getByRole("button", { name: "Создать организацию" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  const orgUrl = page.url();
  return { ctx, page, orgUrl, orgId: orgUrl.split("/o/")[1] };
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

async function createAccount(page: Page, orgUrl: string, code: string, name: string) {
  await page.goto(`${orgUrl}/accounting/accounts`);
  await page.getByRole("button", { name: "Новый счёт" }).first().click();
  const drawer = page.locator("dialog.drawer");
  await drawer.getByLabel("Код счёта").fill(code);
  await drawer.getByLabel("Название").fill(name);
  await drawer.getByRole("button", { name: "Сохранить" }).click();
  await expect(page.getByRole("link", { name: code, exact: true })).toBeVisible();
}

async function pickAccount(page: Page, label: string, code: string) {
  const picker = page.getByRole("combobox", { name: label });
  await picker.click();
  await picker.fill(code);
  await page.getByRole("option", { name: new RegExp(code) }).click();
}

test("бухгалтерия: план счетов, проводка с контролем баланса, сторно, ведомость, закрытие периода", async ({ browser }) => {
  const o = await ownerWithOrg(browser);

  await o.page.goto(`${o.orgUrl}/accounting/accounts`);
  await expect(o.page.getByText(/План счетов пуст/)).toBeVisible(); // предзаполненных счетов нет
  await createAccount(o.page, o.orgUrl, "1000", "Тестовый счёт А");
  await createAccount(o.page, o.orgUrl, "2000", "Тестовый счёт Б");

  // проводка: сначала дебет ≠ кредит — провести нельзя
  await o.page.goto(`${o.orgUrl}/accounting/entries/new`);
  await o.page.getByLabel("Содержание").fill("Тестовая проводка");
  await pickAccount(o.page, "Счёт 1", "1000");
  await pickAccount(o.page, "Счёт 2", "2000");
  await o.page.getByLabel("Дебет 1").fill("150");
  await o.page.getByLabel("Кредит 2").fill("100");
  await expect(o.page.getByTestId("balance-status")).toContainText("Дебет и кредит не равны: разница 50");
  await expect(o.page.getByRole("button", { name: "Провести" })).toBeDisabled();
  await o.page.getByLabel("Кредит 2").fill("150");
  await expect(o.page.getByTestId("balance-status")).toContainText("Дебет и кредит равны.");
  await o.page.getByRole("button", { name: "Провести" }).click();
  await expect(o.page).toHaveURL(/\/accounting\/entries\/[0-9a-f-]{36}$/);
  await expect(o.page.getByRole("heading", { name: /№ 1/ })).toContainText("Проведена");

  // сторно
  await o.page.getByRole("button", { name: "Сторнировать" }).click();
  await o.page.getByRole("dialog").getByRole("button", { name: "Сторнировать" }).click();
  await expect(o.page.getByRole("heading", { name: /№ 2/ })).toContainText("Сторно");
  await expect(o.page.getByRole("button", { name: "Сторнировать" })).toHaveCount(0);

  // журнал и оборотно-сальдовая: обороты равны, сальдо нулевое
  await o.page.goto(`${o.orgUrl}/accounting`);
  await expect(o.page.getByText("Сторнирована")).toBeVisible();
  await o.page.goto(`${o.orgUrl}/accounting/trial-balance`);
  await expect(o.page.getByText("Обороты дебета и кредита равны.")).toBeVisible();
  await expect(o.page.getByRole("link", { name: "1000", exact: true })).toBeVisible();
  await o.page.getByRole("link", { name: "1000", exact: true }).click();
  await expect(o.page.getByRole("heading", { name: /Карточка счёта: 1000/ })).toBeVisible();

  // закрытие периода блокирует проводку
  await o.page.goto(`${o.orgUrl}/accounting/periods`);
  await o.page.getByRole("button", { name: "Закрыть период" }).click();
  await o.page.getByRole("dialog").getByRole("button", { name: "Закрыть период" }).click();
  await expect(o.page.getByText("Закрыт", { exact: true })).toBeVisible();
  await o.page.goto(`${o.orgUrl}/accounting/entries/new`);
  await o.page.getByLabel("Содержание").fill("В закрытый период");
  await pickAccount(o.page, "Счёт 1", "1000");
  await pickAccount(o.page, "Счёт 2", "2000");
  await o.page.getByLabel("Дебет 1").fill("5");
  await o.page.getByLabel("Кредит 2").fill("5");
  await o.page.getByRole("button", { name: "Провести" }).click();
  await expect(o.page.getByText("Период закрыт: проводку сделать нельзя.")).toBeVisible();
  await o.ctx.close();
});

test("бухгалтерия: права — аудитор читает, кладовщик не видит раздел", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await createAccount(o.page, o.orgUrl, "1000", "Счёт");

  const au = await memberWithRole(browser, o, "Аудитор (только чтение)", "Аудитор Петров");
  await au.page.goto(`${o.orgUrl}/accounting`);
  await expect(au.page.getByRole("heading", { name: "Проводки" })).toBeVisible();
  await expect(au.page.getByRole("link", { name: "Новая проводка" })).toHaveCount(0);
  await au.page.goto(`${o.orgUrl}/accounting/accounts`);
  await expect(au.page.getByRole("button", { name: "Новый счёт" })).toHaveCount(0);

  const keeper = await memberWithRole(browser, o, "Кладовщик", "Кладовщик Алиев");
  await keeper.page.goto(`${o.orgUrl}/accounting`);
  await expect(keeper.page.getByText("У вас нет права просматривать бухгалтерию.")).toBeVisible();
  await expect(keeper.page.getByRole("link", { name: "Бухгалтерия" })).toHaveCount(0);
  await au.ctx.close();
  await keeper.ctx.close();
  await o.ctx.close();
});

test("бухгалтерия на узбекском и английском", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.goto(`${o.orgUrl}/accounting`);
  await o.page.getByLabel("Язык").selectOption("uz");
  await expect(o.page.getByRole("heading", { name: "Provodkalar" })).toBeVisible();
  await expect(o.page.getByText("Hozircha provodkalar yoʻq.")).toBeVisible();
  await o.page.getByLabel("Til").selectOption("en");
  await expect(o.page.getByRole("heading", { name: "Journal entries" })).toBeVisible();
  await expect(o.page.getByText("No journal entries yet.")).toBeVisible();
  await o.ctx.close();
});
