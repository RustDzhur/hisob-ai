import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";
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
  await page.getByLabel("Название организации").fill(`ООО Склад ${uniq()}`);
  await page.getByRole("button", { name: "Создать организацию" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  const orgUrl = page.url();
  return { ctx, page, orgUrl, orgId: orgUrl.split("/o/")[1] };
}

type Org = { ctx: BrowserContext; page: Page; orgId: string; orgUrl: string };

async function apiPost(o: Org, path: string, data: unknown) {
  const csrf = (await o.ctx.cookies()).find((c) => c.name === "hisob_csrf")!.value;
  const r = await o.page.request.post(`/api/v1/organizations/${o.orgId}${path}`, { data, headers: { "X-CSRF-Token": csrf } });
  expect(r.status()).toBe(201);
  return r.json();
}

async function seed(o: Org) {
  const units = await (await o.page.request.get(`/api/v1/organizations/${o.orgId}/units`)).json();
  const pcs = units.find((u: { code: string }) => u.code === "pcs").id;
  await apiPost(o, "/warehouses", { name: "Основной склад" });
  await apiPost(o, "/products", { kind: "goods", sku: "MILK-1", name: "Молоко 3%", unit_id: pcs, barcodes: [] });
}

async function newOperation(page: Page, orgUrl: string, kind: string, qty: string) {
  await page.goto(`${orgUrl}/stock/operations/new`);
  await page.getByLabel("Вид операции").selectOption({ label: kind });
  await page.locator("#o-wh").selectOption({ label: "Основной склад" });
  const picker = page.getByRole("combobox", { name: "Товар 1" });
  await picker.click();
  await picker.fill("MILK");
  await page.getByRole("option", { name: /MILK-1/ }).click();
  await page.getByLabel("Количество 1").fill(qty);
  await page.getByRole("button", { name: "Создать черновик" }).click();
  await expect(page).toHaveURL(/\/stock\/operations\/[0-9a-f-]{36}$/);
}

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

test("склад: приход, нехватка при расходе, проведение, остатки, история и сверка", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await seed(o);

  // приход 10: черновик -> проведение
  await newOperation(o.page, o.orgUrl, "Приход", "10");
  await expect(o.page.getByRole("heading", { name: /Приход/ })).toContainText("Черновик");
  await o.page.getByRole("button", { name: "Провести", exact: true }).click();
  await o.page.getByRole("dialog").getByRole("button", { name: "Провести" }).click();
  await expect(o.page.getByRole("heading", { name: /Приход/ })).toContainText("Проведена");
  await expect(o.page.getByText("Проведённая операция только для просмотра.")).toBeVisible();
  await expect(o.page.getByRole("button", { name: "Удалить черновик" })).toHaveCount(0);

  // остатки
  await o.page.goto(`${o.orgUrl}/stock`);
  const row = o.page.getByRole("row", { name: /MILK-1/ }).or(o.page.getByText("MILK-1").first());
  await expect(row.first()).toBeVisible();
  await expect(o.page.getByText(/Сверка: остатки совпадают с журналом движений/)).toBeVisible();

  // расход 15 > 10: явная причина, остаток не меняется, затем исправление до 4
  await newOperation(o.page, o.orgUrl, "Расход", "15");
  await o.page.getByRole("button", { name: "Провести", exact: true }).click();
  await o.page.getByRole("dialog").getByRole("button", { name: "Провести" }).click();
  await expect(o.page.getByText("Недостаточно остатка для проведения.")).toBeVisible();
  await expect(o.page.getByText(/Не хватает: MILK-1 — нужно 15, на складе 10/)).toBeVisible();
  await expect(o.page.getByRole("heading", { name: /Расход/ })).toContainText("Черновик");
  await o.page.getByLabel("Количество 1").fill("4");
  await o.page.getByRole("button", { name: "Сохранить", exact: true }).click();
  await expect(o.page.getByText("Сохранено.")).toBeVisible();
  await o.page.getByRole("button", { name: "Провести", exact: true }).click();
  await o.page.getByRole("dialog").getByRole("button", { name: "Провести" }).click();
  await expect(o.page.getByRole("heading", { name: /Расход/ })).toContainText("Проведена");

  // остаток 6, история с двумя движениями
  await o.page.goto(`${o.orgUrl}/stock`);
  await expect(o.page.getByText("MILK-1").first()).toBeVisible();
  await expect(o.page.getByText(/^6$/).first()).toBeVisible();
  await o.page.getByRole("link", { name: "История" }).first().click();
  await expect(o.page.getByText("+10").first()).toBeVisible();
  await expect(o.page.getByText("-4").first()).toBeVisible();
  await o.ctx.close();
});

test("склад: черновик можно удалить; склад с остатком нельзя архивировать", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await seed(o);
  await newOperation(o.page, o.orgUrl, "Приход", "3");
  await o.page.getByRole("button", { name: "Удалить черновик" }).click();
  await o.page.getByRole("dialog").getByRole("button", { name: "Удалить черновик" }).click();
  await expect(o.page).toHaveURL(/\/stock\/operations$/);
  await expect(o.page.getByText("Операций пока нет.")).toBeVisible();

  await newOperation(o.page, o.orgUrl, "Приход", "3");
  await o.page.getByRole("button", { name: "Провести", exact: true }).click();
  await o.page.getByRole("dialog").getByRole("button", { name: "Провести" }).click();
  await expect(o.page.getByRole("heading", { name: /Приход/ })).toContainText("Проведена");

  await o.page.goto(`${o.orgUrl}/warehouses`);
  await o.page.getByRole("button", { name: "В архив" }).first().click();
  const dialog = o.page.getByRole("dialog");
  await dialog.getByRole("button", { name: /Подтвердить|В архив|Архивировать/ }).click();
  await expect(o.page.getByText("На складе есть остаток: сначала спишите или переместите его.")).toBeVisible();
  await o.ctx.close();
});

test("склад: права — аудитор читает, но не создаёт и не проводит; кладовщик проводит", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await seed(o);
  await newOperation(o.page, o.orgUrl, "Приход", "5");
  const opUrl = o.page.url();

  const au = await memberWithRole(browser, o, "Аудитор (только чтение)", "Аудитор Петров");
  await au.page.goto(`${o.orgUrl}/stock/operations`);
  await expect(au.page.getByRole("heading", { name: "Складские операции" })).toBeVisible();
  await expect(au.page.getByRole("link", { name: "Новая операция" })).toHaveCount(0);
  await au.page.goto(opUrl);
  await expect(au.page.getByRole("button", { name: /Провести|Сохранить|Удалить черновик/ })).toHaveCount(0);

  const keeper = await memberWithRole(browser, o, "Кладовщик", "Кладовщик Алиев");
  await keeper.page.goto(opUrl);
  await keeper.page.getByRole("button", { name: "Провести", exact: true }).click();
  await keeper.page.getByRole("dialog").getByRole("button", { name: "Провести" }).click();
  await expect(keeper.page.getByRole("heading", { name: /Приход/ })).toContainText("Проведена");

  await au.ctx.close();
  await keeper.ctx.close();
  await o.ctx.close();
});

test("склад на узбекском и английском", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.goto(`${o.orgUrl}/stock`);
  await o.page.getByLabel("Язык").selectOption("uz");
  await expect(o.page.getByRole("heading", { name: "Omborlardagi qoldiqlar" })).toBeVisible();
  await expect(o.page.getByText("Hozircha qoldiq yoʻq. Kirim rasmiylashtiring.")).toBeVisible();
  await o.page.getByLabel("Til").selectOption("en");
  await expect(o.page.getByRole("heading", { name: "Stock balances" })).toBeVisible();
  await expect(o.page.getByText("No balances yet. Record a receipt.")).toBeVisible();
  await o.ctx.close();
});

const pdf = (tag: string) => Buffer.from(`%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n%${tag}\n`);

const cpCache = new Map<string, { id: string }>();

async function approvedDocument(o: Org, productId: string, unitId: string, quantity: string) {
  const csrf = (await o.ctx.cookies()).find((c) => c.name === "hisob_csrf")!.value;
  const headers = { "X-CSRF-Token": csrf };
  const base = `/api/v1/organizations/${o.orgId}`;
  const cp = cpCache.get(o.orgId) ?? (await apiPost(o, "/counterparties", { name: "ООО Поставщик", tin: "123456789" }));
  cpCache.set(o.orgId, cp);
  const up = await o.page.request.post(`${base}/documents`, {
    multipart: { file: { name: "inv.pdf", mimeType: "application/pdf", buffer: pdf(uniq()) }, doc_type: "invoice" },
    headers,
  });
  expect(up.status()).toBe(201);
  const doc = await up.json();
  const amount = String(Number(quantity) * 100);
  const patch = await o.page.request.patch(`${base}/documents/${doc.id}`, {
    headers,
    data: {
      expected_version: doc.version, doc_type: "invoice", number: `НАК-${uniq()}`, doc_date: "2026-10-01", counterparty_id: cp.id,
      currency: "UZS", total_amount: amount,
      lines: [{ description: "Молоко", product_id: productId, unit_id: unitId, quantity, unit_price: "100", amount }],
    },
  });
  expect(patch.status()).toBe(200);
  const ok = await o.page.request.post(`${base}/documents/${doc.id}/approve`, { headers, data: { expected_version: (await patch.json()).version, acknowledged: [] } });
  expect(ok.status()).toBe(200);
  return doc.id as string;
}

async function seedWithIds(o: Org) {
  const units = await (await o.page.request.get(`/api/v1/organizations/${o.orgId}/units`)).json();
  const pcs = units.find((u: { code: string }) => u.code === "pcs").id;
  const kg = units.find((u: { code: string }) => u.code === "kg").id;
  await apiPost(o, "/warehouses", { name: "Основной склад" });
  const product = await apiPost(o, "/products", { kind: "goods", sku: "MILK-1", name: "Молоко 3%", unit_id: pcs, barcodes: [] });
  return { pcs, kg, productId: product.id as string };
}

test("склад по документу: оформление, проведение, сторно; несовпадение единиц объясняется", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  const { pcs, kg, productId } = await seedWithIds(o);

  // документ в килограммах при товаре в штуках: понятная причина, ничего не создаётся
  const bad = await approvedDocument(o, productId, kg, "5");
  await o.page.goto(`${o.orgUrl}/documents/${bad}`);
  await o.page.getByRole("button", { name: "Оформить складскую операцию" }).click();
  await o.page.getByLabel("Склад", { exact: true }).selectOption({ label: "Основной склад" });
  await o.page.getByRole("button", { name: "Создать черновик" }).click();
  await expect(o.page.getByText(/Строка 1: единица в документе отличается от единицы товара MILK-1/)).toBeVisible();

  // подходящий документ: черновик по документу -> проведение -> сторно
  const good = await approvedDocument(o, productId, pcs, "8");
  await o.page.goto(`${o.orgUrl}/documents/${good}`);
  await expect(o.page.getByText("По документу складских операций ещё нет.")).toBeVisible();
  await o.page.getByRole("button", { name: "Оформить складскую операцию" }).click();
  await o.page.getByLabel("Склад", { exact: true }).selectOption({ label: "Основной склад" });
  await o.page.getByRole("button", { name: "Создать черновик" }).click();
  await expect(o.page).toHaveURL(/\/stock\/operations\/[0-9a-f-]{36}$/);
  await expect(o.page.getByText(/Документ-основание/)).toBeVisible();
  await o.page.getByRole("button", { name: "Провести", exact: true }).click();
  await o.page.getByRole("dialog").getByRole("button", { name: "Провести" }).click();
  await expect(o.page.getByRole("heading", { name: /Приход/ })).toContainText("Проведена");

  await o.page.getByRole("button", { name: "Сторнировать" }).click();
  await o.page.getByRole("dialog").getByRole("button", { name: "Сторнировать" }).click();
  await expect(o.page.getByText("Это сторно другой операции.")).toBeVisible();
  await expect(o.page.getByRole("button", { name: "Сторнировать" })).toHaveCount(0);

  await o.page.goto(`${o.orgUrl}/stock/operations`);
  await expect(o.page.getByText("Сторнирована")).toBeVisible();
  await expect(o.page.getByText("Сторно", { exact: true }).first()).toBeVisible();
  await o.page.goto(`${o.orgUrl}/stock`);
  await expect(o.page.getByText("Остатков пока нет. Оформите приход.")).toBeVisible(); // остаток вернулся к нулю
  await o.ctx.close();
});
