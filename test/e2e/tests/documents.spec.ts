import { expect, test, type Browser, type Page } from "@playwright/test";
import { newContext } from "./helpers";

const PASSWORD = "correct horse battery";
const uniq = () => `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
const pdf = (tag: string) => Buffer.from(`%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n%${tag}\n`);
// минимальный валидный PNG 1x1
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");

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
  await page.getByLabel("Название организации").fill(`ООО Документы ${uniq()}`);
  await page.getByRole("button", { name: "Создать организацию" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  const orgUrl = page.url();
  return { ctx, page, orgUrl, orgId: orgUrl.split("/o/")[1] };
}

async function apiPost(o: { ctx: import("@playwright/test").BrowserContext; page: Page; orgId: string }, path: string, data: unknown) {
  const csrf = (await o.ctx.cookies()).find((c) => c.name === "hisob_csrf")!.value;
  const r = await o.page.request.post(`/api/v1/organizations/${o.orgId}${path}`, { data, headers: { "X-CSRF-Token": csrf } });
  expect(r.status()).toBe(201);
  return r.json();
}

async function uploadViaUi(page: Page, orgUrl: string, name: string, buffer: Buffer, mimeType = "application/pdf") {
  await page.goto(`${orgUrl}/documents`);
  await page.getByRole("button", { name: "Загрузить документ" }).first().click();
  const drawer = page.locator("dialog.drawer");
  await drawer.locator("input[type=file]").first().setInputFiles({ name, mimeType, buffer });
  await drawer.getByRole("button", { name: "Загрузить", exact: true }).click();
  return drawer;
}

test("документ: загрузка, ручная проверка, предупреждение, утверждение, возврат и отклонение", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await apiPost(o, "/counterparties", { name: "ООО Поставщик Альфа", tin: "123456789" });

  const drawer = await uploadViaUi(o.page, o.orgUrl, "invoice-1.pdf", pdf(uniq()));
  await expect(drawer.getByText("Загружен.")).toBeVisible();
  await drawer.getByRole("link", { name: "Открыть" }).click();

  // карточка: оригинал, статус, блокирующие ошибки
  await expect(o.page.getByRole("heading", { name: /Прочее|Счёт/ })).toContainText("На проверке");
  await expect(o.page.locator("object[type='application/pdf']")).toBeAttached();
  await expect(o.page.getByText("Укажите номер документа.")).toBeVisible();
  await expect(o.page.getByRole("button", { name: "Утвердить" })).toBeDisabled();

  // заполнение полей и строки
  await o.page.getByLabel("Номер", { exact: true }).fill("INV-1");
  await o.page.getByLabel("Дата", { exact: true }).fill("2026-10-01");
  await o.page.getByRole("combobox", { name: "Контрагент" }).click();
  await o.page.getByRole("combobox", { name: "Контрагент" }).fill("Альфа");
  await o.page.getByRole("option", { name: /Поставщик Альфа/ }).click();
  await o.page.getByLabel("Валюта", { exact: true }).selectOption("UZS");
  await o.page.getByLabel("Итого по документу").fill("200");
  await o.page.getByRole("button", { name: "Добавить строку" }).click();
  await o.page.getByLabel("Наименование 1").fill("Молоко");
  await o.page.getByLabel("Кол-во 1").fill("2");
  await o.page.getByLabel("Цена 1").fill("100");
  await o.page.getByLabel("Сумма 1").fill("200");
  await expect(o.page.getByText("Есть несохранённые изменения")).toBeVisible();
  await expect(o.page.getByRole("button", { name: "Утвердить" })).toBeDisabled(); // сначала сохранить
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(o.page.getByText("Замечаний нет.")).toBeVisible();
  await expect(o.page.getByRole("button", { name: "Утвердить" })).toBeEnabled();

  // расхождение сумм -> предупреждение, которое нужно подтвердить
  await o.page.getByLabel("Итого по документу").fill("999");
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(o.page.getByText(/Итог 999\.00 не совпадает/)).toBeVisible();
  await o.page.getByRole("button", { name: "Утвердить" }).click();
  const modal = o.page.getByRole("dialog");
  const confirm = modal.getByRole("button", { name: "Утвердить" });
  await expect(confirm).toBeDisabled(); // пока предупреждение не подтверждено
  await modal.getByRole("checkbox").check();
  await confirm.click();
  await expect(o.page.getByRole("heading", { name: /INV-1/ })).toContainText("Утверждён");
  await expect(o.page.getByLabel("Номер", { exact: true })).toBeDisabled(); // утверждённый документ не редактируется
  await expect(o.page.getByText("Документ в этом статусе не редактируется.")).toBeVisible();

  // возврат на проверку и отклонение с причиной
  await o.page.getByRole("button", { name: "Вернуть на проверку" }).click();
  await expect(o.page.getByRole("heading", { name: /INV-1/ })).toContainText("На проверке");
  await o.page.getByRole("button", { name: "Отклонить" }).click();
  await o.page.getByRole("dialog").getByLabel("Причина").fill("Не наш документ");
  await o.page.getByRole("dialog").getByRole("button", { name: "Отклонить" }).click();
  await expect(o.page.getByRole("heading", { name: /INV-1/ })).toContainText("Отклонён");
  await expect(o.page.getByText("Причина отклонения: Не наш документ")).toBeVisible();

  // список и журнал аудита видят действия
  await o.page.getByRole("link", { name: "← К списку документов" }).click();
  await o.page.getByLabel("Статус", { exact: true }).selectOption("rejected");
  await expect(o.page.getByRole("table").getByRole("row", { name: /INV-1/ })).toContainText("ООО Поставщик Альфа");
  await o.page.getByRole("link", { name: "Журнал аудита" }).click();
  await expect(o.page.getByRole("table").getByText("Утверждение документа")).toBeVisible();
  await expect(o.page.getByRole("table").getByText("Загрузка документа")).toBeVisible();
  await o.ctx.close();
});

test("загрузка: дубликат по содержимому, неподходящие и опасные файлы, изображение", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  const same = pdf(uniq());

  let drawer = await uploadViaUi(o.page, o.orgUrl, "first.pdf", same);
  await expect(drawer.getByText("Загружен.")).toBeVisible();
  await o.page.keyboard.press("Escape");

  // тот же файл под другим именем
  drawer = await uploadViaUi(o.page, o.orgUrl, "copy-of-first.pdf", same);
  await expect(drawer.getByText("Уже загружен.")).toBeVisible();
  await expect(drawer.getByRole("link", { name: "Открыть имеющийся" })).toBeVisible();
  await o.page.keyboard.press("Escape");
  await expect(o.page.getByText("Всего: 1")).toBeVisible(); // дубликат документа не создал

  // расширение не соответствует содержимому; опасный PDF
  drawer = await uploadViaUi(o.page, o.orgUrl, "fake.pdf", PNG, "application/pdf");
  await expect(drawer.getByText("Расширение не соответствует содержимому файла.")).toBeVisible();
  await o.page.keyboard.press("Escape");
  drawer = await uploadViaUi(o.page, o.orgUrl, "evil.pdf", Buffer.from(`%PDF-1.4\n/JavaScript (app.alert(1))\n%${uniq()}\n`));
  await expect(drawer.getByText(/отклонён по соображениям безопасности/)).toBeVisible();
  await o.page.keyboard.press("Escape");
  drawer = await uploadViaUi(o.page, o.orgUrl, "run.exe", Buffer.from("MZ\x90\x00 not a document"), "application/octet-stream");
  await expect(drawer.getByText("Поддерживаются PDF, JPEG, PNG, XLSX и CSV.")).toBeVisible();
  await o.page.keyboard.press("Escape");

  // изображение показывается прямо в карточке
  drawer = await uploadViaUi(o.page, o.orgUrl, "scan.png", PNG, "image/png");
  await drawer.getByRole("link", { name: "Открыть" }).click();
  await expect(o.page.locator("section.viewer img")).toBeVisible();
  await o.ctx.close();
});

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

test("права: оператор загружает и правит, но не утверждает; аудитор только читает", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  const drawer = await uploadViaUi(o.page, o.orgUrl, "shared.pdf", pdf(uniq()));
  await expect(drawer.getByText("Загружен.")).toBeVisible();
  await drawer.getByRole("link", { name: "Открыть" }).click();
  await expect(o.page).toHaveURL(/\/documents\/[0-9a-f-]{36}$/);
  const docUrl = o.page.url();

  const op = await memberWithRole(browser, o, "Оператор документов", "Оператор Сидоров");
  await op.page.goto(docUrl);
  await expect(op.page.getByLabel("Номер", { exact: true })).toBeEnabled();
  await expect(op.page.getByRole("button", { name: "Отклонить" })).toBeVisible();
  await expect(op.page.getByRole("button", { name: "Утвердить" })).toHaveCount(0);
  await op.page.getByLabel("Номер", { exact: true }).fill("OP-1");
  await op.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(op.page.getByRole("heading", { name: /OP-1/ })).toBeVisible();

  const au = await memberWithRole(browser, o, "Аудитор (только чтение)", "Аудитор Петров");
  await au.page.goto(`${o.orgUrl}/documents`);
  await expect(au.page.getByRole("button", { name: "Загрузить документ" })).toHaveCount(0);
  await au.page.getByRole("link", { name: "OP-1" }).click();
  await expect(au.page.getByLabel("Номер", { exact: true })).toBeDisabled();
  await expect(au.page.getByRole("button", { name: /Сохранить|Утвердить|Отклонить/ })).toHaveCount(0);

  await op.ctx.close();
  await au.ctx.close();
  await o.ctx.close();
});

test("документы на узбекском и английском", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.goto(`${o.orgUrl}/documents`);
  await o.page.getByLabel("Язык").selectOption("uz");
  await expect(o.page.getByRole("heading", { name: "Hujjatlar" })).toBeVisible();
  await expect(o.page.getByText("Hozircha hujjatlar yoʻq. Birinchi faylni yuklang.")).toBeVisible();
  await o.page.getByLabel("Til").selectOption("en");
  await expect(o.page.getByRole("heading", { name: "Documents" })).toBeVisible();
  await expect(o.page.getByText("No documents yet. Upload the first file.")).toBeVisible();
  await o.ctx.close();
});

test("распознавание: панель AI видна, без запуска платных запросов", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  const drawer = await uploadViaUi(o.page, o.orgUrl, "ai-1.pdf", pdf(uniq()));
  // в окне загрузки есть переключатель распознавания (доступен только если провайдер настроен)
  await expect(drawer.getByText("Распознать сразу (AI)")).toBeVisible();
  await expect(drawer.getByText("Загружен.")).toBeVisible();
  await drawer.getByRole("link", { name: "Открыть" }).click();

  const panel = o.page.getByTestId("ai-panel");
  await expect(panel.getByRole("heading", { name: "Распознавание (AI)" })).toBeVisible();
  await expect(panel.getByText("AI только предлагает значения")).toBeVisible();
  // либо кнопка запуска, либо честное сообщение «не настроено» — но не мёртвая кнопка
  const run = panel.getByRole("button", { name: "Распознать", exact: true });
  const off = panel.getByText("Распознавание не настроено");
  await expect(run.or(off)).toBeVisible();
  await expect(panel.getByText("MOCK")).toHaveCount(0);
  await o.ctx.close();
});
