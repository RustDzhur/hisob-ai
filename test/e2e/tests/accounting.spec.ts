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
  await expect(o.page.getByText("Период закрыт: проводку сделать нельзя.").first()).toBeVisible();
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

const pdfBytes = (tag: string) => Buffer.from(`%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n%${tag}\n`);

async function apiJson(o: Org, method: "post" | "patch", path: string, data: unknown, status = 201) {
  const csrf = (await o.ctx.cookies()).find((c) => c.name === "hisob_csrf")!.value;
  const r = await o.page.request[method](`/api/v1/organizations/${o.orgId}${path}`, { data, headers: { "X-CSRF-Token": csrf } });
  expect(r.status()).toBe(status);
  return r.json();
}

test("правила проводок: ввод и утверждение бухгалтером, предложение и проведение по документу", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  for (const [code, name] of [["1000", "Покупатели"], ["2000", "Выручка"], ["3000", "Налог"]]) await apiJson(o, "post", "/accounting/accounts", { code, name });

  await o.page.goto(`${o.orgUrl}/accounting/rules`);
  await expect(o.page.getByText(/Правил пока нет/)).toBeVisible(); // готовых правил в системе нет
  await o.page.getByRole("link", { name: "Новое правило" }).first().click();
  await o.page.getByLabel("Ключ правила").fill("sales");
  await o.page.getByLabel("Название", { exact: true }).fill("Реализация услуг");
  await pickAccount(o.page, "Счёт 1", "1000");
  await pickAccount(o.page, "Счёт 2", "2000");
  await o.page.getByLabel("Сторона 2").selectOption("credit");
  await o.page.getByLabel("Сумма из документа 2").selectOption("total_minus_tax");
  // пока налоговой строки нет: Дт итого ≠ Кт итого минус налог — сохранить нельзя
  await expect(o.page.getByTestId("rule-balance")).toContainText("должно быть сбалансировано");
  await expect(o.page.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  await o.page.getByRole("button", { name: "Добавить строку" }).click();
  await pickAccount(o.page, "Счёт 3", "3000");
  await o.page.getByLabel("Сумма из документа 3").selectOption("tax_total");
  await expect(o.page.getByTestId("rule-balance")).toContainText("Дебет и кредит равны.");
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(o.page).toHaveURL(/\/accounting\/rules\/[0-9a-f-]{36}$/);
  await expect(o.page.getByRole("heading", { name: /Реализация услуг/ })).toContainText("Черновик");

  // утверждение требует основания
  await o.page.getByRole("button", { name: "Утвердить", exact: true }).click();
  const dialog = o.page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "Утвердить" })).toBeDisabled();
  await dialog.getByLabel("Основание").fill("Решение бухгалтера от 10.10.2026");
  await dialog.getByRole("button", { name: "Утвердить" }).click();
  await expect(o.page.getByRole("heading", { name: /Реализация услуг/ })).toContainText("Утверждено");
  await expect(o.page.getByText("Утверждённое правило только для просмотра.")).toBeVisible();

  // документ: итог 1120, налог 120
  const csrf = (await o.ctx.cookies()).find((c) => c.name === "hisob_csrf")!.value;
  const up = await o.page.request.post(`/api/v1/organizations/${o.orgId}/documents`, {
    multipart: { file: { name: "inv.pdf", mimeType: "application/pdf", buffer: pdfBytes(uniq()) }, doc_type: "invoice" },
    headers: { "X-CSRF-Token": csrf },
  });
  const doc = await up.json();
  const cp = await apiJson(o, "post", "/counterparties", { name: "ООО Клиент", tin: "123456789" });
  const patched = await apiJson(o, "patch", `/documents/${doc.id}`, {
    expected_version: doc.version, doc_type: "invoice", number: "СФ-1", doc_date: "2026-10-05", counterparty_id: cp.id, currency: "UZS",
    total_amount: "1120", tax_total: "120", lines: [{ description: "Услуга", quantity: "1", unit_price: "1000", amount: "1000" }],
  }, 200);
  await apiJson(o, "post", `/documents/${doc.id}/approve`, { expected_version: patched.version, acknowledged: [] }, 200);

  await o.page.goto(`${o.orgUrl}/documents/${doc.id}`);
  const panel = o.page.getByTestId("doc-journal");
  await expect(panel.getByText("Реализация услуг")).toBeVisible();
  await expect(panel.getByText(/1000 · Покупатели/)).toBeVisible();
  await panel.getByRole("button", { name: "Провести по правилу" }).click();
  await expect(panel.getByText("Уже проведено")).toBeVisible();
  await panel.getByRole("link", { name: "Открыть проводку" }).click();
  await expect(o.page.getByRole("heading", { name: /№ 1/ })).toContainText("Проведена");
  await expect(o.page.getByText(/Документ-основание/)).toBeVisible();
  await expect(o.page.getByText(/Правило: Реализация услуг/)).toBeVisible();
  await o.ctx.close();
});

test("правила проводок на узбекском и английском", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.goto(`${o.orgUrl}/accounting/rules`);
  await o.page.getByLabel("Язык").selectOption("uz");
  await expect(o.page.getByRole("heading", { name: "Provodka qoidalari" })).toBeVisible();
  await expect(o.page.getByText(/Hozircha qoidalar yoʻq/)).toBeVisible();
  await o.page.getByLabel("Til").selectOption("en");
  await expect(o.page.getByRole("heading", { name: "Posting rules" })).toBeVisible();
  await expect(o.page.getByText(/No rules yet/)).toBeVisible();
  await o.ctx.close();
});

test("курсы валют, налоговый код и отчёт в базовой валюте", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  const csrf = (await o.ctx.cookies()).find((c) => c.name === "hisob_csrf")!.value;
  const headers = { "X-CSRF-Token": csrf };
  const base = `/api/v1/organizations/${o.orgId}`;
  expect((await o.page.request.put(`${base}/currencies/USD`, { data: { enabled: true }, headers })).status()).toBe(200);
  const cash = await apiJson(o, "post", "/accounting/accounts", { code: "1000", name: "Касса" });
  const inc = await apiJson(o, "post", "/accounting/accounts", { code: "9000", name: "Доходы" });
  const entry = (currency: string, amount: string) => ({
    entry_date: "2026-10-06", currency, description: `Проводка ${currency}`,
    lines: [{ account_id: cash.id, debit: amount, credit: "0" }, { account_id: inc.id, debit: "0", credit: amount }],
  });
  await apiJson(o, "post", "/accounting/entries", entry("UZS", "100"));
  await apiJson(o, "post", "/accounting/entries", entry("USD", "10"));

  // отчёт в базовой валюте без курса: честно «неполный»
  await o.page.goto(`${o.orgUrl}/accounting/trial-balance`);
  await o.page.getByLabel(/В базовой валюте/).check();
  await expect(o.page.getByText("Отчёт неполный: нет курсов для части проводок.")).toBeVisible();

  // курс вручную -> отчёт полный
  await o.page.goto(`${o.orgUrl}/accounting/rates`);
  await expect(o.page.getByText("Курсов пока нет.")).toBeVisible();
  await expect(o.page.getByRole("button", { name: "Загрузить курсы ЦБ" })).toBeVisible(); // базовая валюта — сум
  await o.page.getByLabel("Дата курса").first().fill("2026-10-05");
  await o.page.getByLabel(/Курс \(в базовой валюте/).fill("12000,5");
  await o.page.getByRole("button", { name: "Сохранить курс" }).click();
  await expect(o.page.getByRole("cell", { name: "USD" })).toBeVisible();
  await expect(o.page.getByRole("cell", { name: "Вручную", exact: true })).toBeVisible();
  await o.page.goto(`${o.orgUrl}/accounting/trial-balance`);
  await o.page.getByLabel(/В базовой валюте/).check();
  await expect(o.page.getByText("Обороты дебета и кредита равны.")).toBeVisible();
  await expect(o.page.getByText("Отчёт неполный")).toHaveCount(0);
  await expect(o.page.getByText("Справочный пересчёт по курсу на дату проводки; не переоценка.")).toBeVisible();
  const csv = await o.page.request.get(`${base}/accounting/reports/trial-balance.csv?date_from=2026-10-01&date_to=2026-10-31&base=true`);
  expect(csv.status()).toBe(200);
  expect(await csv.text()).toContain("120105.00");

  // налоговый код: справочная запись — только черновик, пока бухгалтер не подтвердит
  await o.page.goto(`${o.orgUrl}/accounting/taxes`);
  await expect(o.page.getByText("Налоговых кодов пока нет.")).toBeVisible();
  await o.page.getByRole("button", { name: "Добавить справочный НДС 12 %" }).click();
  const row = o.page.getByRole("row", { name: /VAT-12/ });
  await expect(row).toContainText("Черновик");
  await expect(row).toContainText("Бухгалтер обязан проверить");
  await row.getByRole("button", { name: "Подтвердить" }).click();
  await expect(o.page.getByRole("row", { name: /VAT-12/ })).toContainText("Подтверждён");
  await o.page.getByRole("row", { name: /VAT-12/ }).getByRole("button", { name: "Сделать основным" }).click();
  await expect(o.page.getByRole("row", { name: /VAT-12/ })).toContainText("Основной");
  await o.ctx.close();
});

test("курсы и налоги на узбекском и английском", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.goto(`${o.orgUrl}/accounting/rates`);
  await o.page.getByLabel("Язык").selectOption("uz");
  await expect(o.page.getByRole("heading", { name: "Valyuta kurslari" })).toBeVisible();
  await o.page.goto(`${o.orgUrl}/accounting/taxes`);
  await expect(o.page.getByText("Hozircha soliq kodlari yoʻq.")).toBeVisible();
  await o.page.getByLabel("Til").selectOption("en");
  await expect(o.page.getByRole("heading", { name: "Tax codes" })).toBeVisible();
  await expect(o.page.getByText("No tax codes yet.")).toBeVisible();
  await o.ctx.close();
});
