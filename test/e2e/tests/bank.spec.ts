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
  await page.getByLabel("Название организации").fill(`ООО Банк ${uniq()}`);
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

const csv = (rows: string[]) => Buffer.from(["Дата;Сумма;Назначение;Номер;Контрагент;ИНН", ...rows, ""].join("\n"), "utf-8");

async function createAccount(page: Page, orgUrl: string) {
  await page.goto(`${orgUrl}/bank/accounts`);
  await expect(page.getByText("Банковских счетов пока нет.")).toBeVisible();
  await page.getByRole("button", { name: "Новый счёт" }).first().click();
  const drawer = page.locator("dialog.drawer");
  await drawer.getByLabel("Название").fill("Расчётный счёт");
  await drawer.getByLabel("Банк").fill("Тест-банк");
  await drawer.getByLabel("Номер счёта").fill("20208000100200300400");
  await drawer.getByRole("button", { name: "Сохранить" }).click();
  await page.getByRole("link", { name: "Расчётный счёт" }).click();
  await expect(page.getByRole("heading", { name: /Расчётный счёт/ })).toBeVisible();
}

test("банк: профиль импорта, предпросмотр с ошибками, загрузка, повтор и перекрытие", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await createAccount(o.page, o.orgUrl);

  // профиль: образец -> колонки -> сопоставление
  await o.page.locator("#bp-h").scrollIntoViewIfNeeded();
  await o.page.getByLabel("Образец выписки (CSV или XLSX)").setInputFiles({ name: "sample.csv", mimeType: "text/csv", buffer: csv(["01.10.2026;100,00;Оплата;P-1;ООО Альфа;123456789"]) });
  await o.page.getByRole("button", { name: "Прочитать колонки" }).click();
  await o.page.getByLabel("Колонка даты").selectOption("Дата");
  await o.page.getByLabel("Колонка суммы").selectOption("Сумма");
  await o.page.getByLabel("Колонка назначения").selectOption("Назначение");
  await o.page.getByLabel("Колонка номера документа").selectOption("Номер");
  await o.page.getByLabel("Колонка контрагента").selectOption("Контрагент");
  await o.page.getByRole("button", { name: "Сохранить профиль" }).click();
  await expect(o.page.getByText("Профиль сохранён.")).toBeVisible();

  // предпросмотр: две хорошие строки и две плохие — загрузка заблокирована, пока не выбран пропуск
  const stmt = csv(["01.10.2026;1 000,00;Оплата по счёту 15;P-1;ООО Альфа;123456789", "02.10.2026;-250,50;Комиссия банка;;;", "xx.10.2026;5;Плохая дата;;;", "04.10.2026;abc;Плохая сумма;;;"]);
  const importFile = o.page.getByLabel("Файл выписки");
  await importFile.setInputFiles({ name: "stmt.csv", mimeType: "text/csv", buffer: stmt });
  await o.page.getByRole("button", { name: "Проверить" }).click();
  await expect(o.page.getByTestId("preview-summary")).toContainText("Строк: 4 · готово к загрузке: 2 · уже есть: 0 · с ошибками: 2");
  await expect(o.page.getByText(/дата не разобрана по выбранному формату/)).toBeVisible();
  await expect(o.page.getByRole("button", { name: "Загрузить", exact: true })).toBeDisabled();
  await o.page.getByLabel("Пропустить строки с ошибками и загрузить остальные").check();
  await o.page.getByRole("button", { name: "Загрузить", exact: true }).click();
  await expect(o.page.getByTestId("import-result")).toContainText("Загружено: 2");

  // операции
  await o.page.goto(`${o.orgUrl}/bank`);
  await expect(o.page.getByText("Оплата по счёту 15")).toBeVisible();
  await expect(o.page.getByText("Комиссия банка")).toBeVisible();
  await expect(o.page.getByText("Не сверена").first()).toBeVisible();

  // тот же файл повторно: предпросмотр сообщает, загрузка недоступна
  await o.page.goBack();
  await o.page.getByLabel("Файл выписки").setInputFiles({ name: "stmt.csv", mimeType: "text/csv", buffer: stmt });
  await o.page.getByRole("button", { name: "Проверить" }).click();
  await expect(o.page.getByText("Этот файл уже загружен по этому счёту.")).toBeVisible();
  await expect(o.page.getByRole("button", { name: "Загрузить", exact: true })).toBeDisabled();

  // перекрывающаяся выписка: старые строки не дублируются
  const overlap = csv(["01.10.2026;1 000,00;Оплата по счёту 15;P-1;ООО Альфа;123456789", "02.10.2026;-250,50;Комиссия банка;;;", "05.10.2026;75,00;Возврат;;;"]);
  await o.page.getByLabel("Файл выписки").setInputFiles({ name: "overlap.csv", mimeType: "text/csv", buffer: overlap });
  await o.page.getByRole("button", { name: "Проверить" }).click();
  await expect(o.page.getByTestId("preview-summary")).toContainText("готово к загрузке: 1 · уже есть: 2");
  await o.page.getByRole("button", { name: "Загрузить", exact: true }).click();
  await expect(o.page.getByTestId("import-result")).toContainText("Загружено: 1");
  await expect(o.page.getByTestId("import-result").locator("..")).toContainText("Пропущено как уже имеющиеся: 2");
  await o.ctx.close();
});

test("банк: права — аудитор читает, но не загружает; кладовщик не видит раздел", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await createAccount(o.page, o.orgUrl);
  const accountUrl = o.page.url();

  const au = await memberWithRole(browser, o, "Аудитор (только чтение)", "Аудитор Петров");
  await au.page.goto(accountUrl);
  await expect(au.page.getByRole("heading", { name: /Расчётный счёт/ })).toBeVisible();
  await expect(au.page.getByRole("heading", { name: "Загрузка выписки" })).toHaveCount(0);
  await expect(au.page.getByRole("heading", { name: "Профиль импорта" })).toHaveCount(0);
  await au.page.goto(`${o.orgUrl}/bank/accounts`);
  await expect(au.page.getByRole("button", { name: "Новый счёт" })).toHaveCount(0);

  const keeper = await memberWithRole(browser, o, "Кладовщик", "Кладовщик Алиев");
  await keeper.page.goto(`${o.orgUrl}/bank`);
  await expect(keeper.page.getByText("У вас нет права просматривать банк.")).toBeVisible();
  await expect(keeper.page.getByRole("link", { name: "Банк", exact: true })).toHaveCount(0);
  await au.ctx.close();
  await keeper.ctx.close();
  await o.ctx.close();
});

test("банк на узбекском и английском", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.goto(`${o.orgUrl}/bank`);
  await o.page.getByLabel("Язык").selectOption("uz");
  await expect(o.page.getByRole("heading", { name: "Bank operatsiyalari" })).toBeVisible();
  await expect(o.page.getByText(/Hozircha operatsiyalar yoʻq/)).toBeVisible();
  await o.page.getByLabel("Til").selectOption("en");
  await expect(o.page.getByRole("heading", { name: "Bank transactions" })).toBeVisible();
  await expect(o.page.getByText(/No transactions yet/)).toBeVisible();
  await o.ctx.close();
});

test("сверка: страница операции, пропуск и возврат в сверку; аудитор не видит кнопок", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await createAccount(o.page, o.orgUrl);
  await o.page.getByLabel("Образец выписки (CSV или XLSX)").setInputFiles({ name: "s.csv", mimeType: "text/csv", buffer: csv(["01.10.2026;100,00;Оплата;P-1;ООО Альфа;123456789"]) });
  await o.page.getByRole("button", { name: "Прочитать колонки" }).click();
  await o.page.getByLabel("Колонка даты").selectOption("Дата");
  await o.page.getByLabel("Колонка суммы").selectOption("Сумма");
  await o.page.getByLabel("Колонка назначения").selectOption("Назначение");
  await o.page.getByRole("button", { name: "Сохранить профиль" }).click();
  await expect(o.page.getByText("Профиль сохранён.")).toBeVisible();
  await o.page.getByLabel("Файл выписки").setInputFiles({ name: "stmt.csv", mimeType: "text/csv", buffer: csv(["01.10.2026;500,00;Оплата за товар;;;"]) });
  await o.page.getByRole("button", { name: "Проверить" }).click();
  await o.page.getByRole("button", { name: "Загрузить", exact: true }).click();
  await expect(o.page.getByTestId("import-result")).toContainText("Загружено: 1");

  await o.page.goto(`${o.orgUrl}/bank`);
  await o.page.getByRole("link", { name: "Сверка" }).first().click();
  await expect(o.page.getByRole("heading", { name: "Банковская операция" })).toBeVisible();
  await expect(o.page.getByTestId("txn-remaining")).toContainText("500");
  await expect(o.page.getByText("Подходящих документов не найдено.")).toBeVisible();

  await o.page.getByRole("button", { name: "Пропустить операцию" }).click();
  await expect(o.page.getByText("Пропущена").first()).toBeVisible();
  await expect(o.page.getByRole("heading", { name: "Предложения (по документам)" })).toHaveCount(0);
  const txnUrl = o.page.url();
  await o.page.getByRole("button", { name: "Вернуть в сверку" }).click();
  await expect(o.page.getByRole("button", { name: "Пропустить операцию" })).toBeVisible();

  const au = await memberWithRole(browser, o, "Аудитор (только чтение)", "Аудитор Петров");
  await au.page.goto(txnUrl);
  await expect(au.page.getByRole("heading", { name: "Банковская операция" })).toBeVisible();
  await expect(au.page.getByRole("button", { name: "Пропустить операцию" })).toHaveCount(0);
  await expect(au.page.getByRole("heading", { name: "Предложения (по документам)" })).toHaveCount(0);
  await au.ctx.close();
  await o.ctx.close();
});

const pdfBytes = (tag: string) => Buffer.from(`%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n%${tag}\n`);

async function apiJson(o: Org, method: "post" | "patch", path: string, data: unknown, status = 201) {
  const csrf = (await o.ctx.cookies()).find((c) => c.name === "hisob_csrf")!.value;
  const r = await o.page.request[method](`/api/v1/organizations/${o.orgId}${path}`, { data, headers: { "X-CSRF-Token": csrf } });
  expect(r.status()).toBe(status);
  return r.json();
}

test("сверка и отчёты: подтверждение предложения, панель документа, отчёт и отмена", async ({ browser }) => {
  const o = await ownerWithOrg(browser);

  // утверждённый документ на 1000
  const csrf = (await o.ctx.cookies()).find((c) => c.name === "hisob_csrf")!.value;
  const up = await o.page.request.post(`/api/v1/organizations/${o.orgId}/documents`, {
    multipart: { file: { name: "inv.pdf", mimeType: "application/pdf", buffer: pdfBytes(uniq()) }, doc_type: "invoice" },
    headers: { "X-CSRF-Token": csrf },
  });
  const doc = await up.json();
  const cp = await apiJson(o, "post", "/counterparties", { name: "ООО Клиент", tin: "123456789" });
  const patched = await apiJson(o, "patch", `/documents/${doc.id}`, {
    expected_version: doc.version, doc_type: "invoice", number: "СФ-77", doc_date: "2026-10-01", counterparty_id: cp.id, currency: "UZS",
    total_amount: "1000", lines: [{ description: "Услуга", quantity: "1", unit_price: "1000", amount: "1000" }],
  }, 200);
  await apiJson(o, "post", `/documents/${doc.id}/approve`, { expected_version: patched.version, acknowledged: [] }, 200);

  // счёт, профиль, выписка
  await createAccount(o.page, o.orgUrl);
  await o.page.getByLabel("Образец выписки (CSV или XLSX)").setInputFiles({ name: "s.csv", mimeType: "text/csv", buffer: csv(["01.10.2026;100,00;Оплата;P-1;ООО Альфа;123456789"]) });
  await o.page.getByRole("button", { name: "Прочитать колонки" }).click();
  await o.page.getByLabel("Колонка даты").selectOption("Дата");
  await o.page.getByLabel("Колонка суммы").selectOption("Сумма");
  await o.page.getByLabel("Колонка назначения").selectOption("Назначение");
  await o.page.getByRole("button", { name: "Сохранить профиль" }).click();
  await expect(o.page.getByText("Профиль сохранён.")).toBeVisible();
  await o.page.getByLabel("Файл выписки").setInputFiles({ name: "stmt.csv", mimeType: "text/csv", buffer: csv(["05.10.2026;1 000,00;Оплата по счёту СФ-77;;;"]) });
  await o.page.getByRole("button", { name: "Проверить" }).click();
  await o.page.getByRole("button", { name: "Загрузить", exact: true }).click();
  await expect(o.page.getByTestId("import-result")).toContainText("Загружено: 1");

  // операция -> предложение с причиной -> подтверждение
  await o.page.goto(`${o.orgUrl}/bank`);
  await o.page.getByRole("link", { name: "Сверка" }).first().click();
  const cand = o.page.getByTestId("txn-candidates");
  await expect(cand.getByText(/СФ-77/).first()).toBeVisible();
  await expect(cand.getByText(/номер документа СФ-77 найден в назначении платежа/)).toBeVisible();
  const txnUrl = o.page.url();
  await cand.getByRole("button", { name: "Подтвердить" }).click();
  await expect(o.page.getByTestId("txn-matches")).toContainText("СФ-77");
  await expect(o.page.getByTestId("txn-remaining")).toContainText("0");
  await expect(o.page.getByText("Сверена").first()).toBeVisible();

  // карточка документа: оплачено полностью
  await o.page.goto(`${o.orgUrl}/documents/${doc.id}`);
  const panel = o.page.getByTestId("doc-bank");
  await expect(panel.getByTestId("doc-paid")).toContainText("1");
  await expect(panel.getByTestId("doc-unpaid")).toContainText("0");

  // отчёты: сводка и сопоставления, переход к списку
  await o.page.goto(`${o.orgUrl}/bank/reports`);
  await o.page.getByLabel("С", { exact: true }).fill("2026-10-01");
  await o.page.getByLabel("По", { exact: true }).fill("2026-10-31");
  await o.page.getByRole("button", { name: "Показать" }).click();
  await expect(o.page.getByTestId("bank-summary")).toContainText("Расчётный счёт");
  await expect(o.page.getByTestId("bank-matches-report")).toContainText("СФ-77");
  await o.page.getByTestId("bank-summary").getByRole("link", { name: "1" }).first().click();
  await expect(o.page).toHaveURL(/\/bank\?/);
  await expect(o.page.getByText("Оплата по счёту СФ-77")).toBeVisible();

  // отмена возвращает операцию в несверенные
  await o.page.goto(txnUrl);
  await o.page.getByRole("button", { name: "Отменить" }).click();
  await expect(o.page.getByText("Сопоставлений пока нет.")).toBeVisible();
  await expect(o.page.getByTestId("txn-remaining")).toContainText("1");
  await o.ctx.close();
});
