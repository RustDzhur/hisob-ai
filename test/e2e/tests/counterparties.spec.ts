import { expect, test, type Browser, type Page } from "@playwright/test";
import { newContext } from "./helpers";

const PASSWORD = "correct horse battery";
const uniq = () => `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;

async function ownerWithOrg(browser: Browser) {
  const ctx = await newContext(browser);
  const page = await ctx.newPage();
  await page.goto("/register");
  await page.getByLabel("Имя и фамилия").fill("Владелец Иванов");
  await page.getByLabel("Email").fill(`owner-${uniq()}@example.uz`);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Зарегистрироваться" }).click();
  await page.getByLabel("Название организации").fill(`ООО Контрагенты ${uniq()}`);
  await page.getByRole("button", { name: "Создать организацию" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  const orgId = page.url().split("/o/")[1];
  return { ctx, page, orgId, orgUrl: page.url() };
}

async function fillDrawer(page: Page, v: { name?: string; tin?: string; customer?: boolean; supplier?: boolean }) {
  if (v.name !== undefined) await page.getByLabel("Название", { exact: true }).fill(v.name);
  if (v.tin !== undefined) await page.getByLabel("ИНН/СТИР", { exact: true }).fill(v.tin);
  if (v.customer) await page.getByLabel("Покупатель").check();
  if (v.supplier) await page.getByLabel("Поставщик").check();
}

test("контрагенты: создание, ошибки, правка, архив и возврат", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.getByRole("link", { name: "Контрагенты" }).click();
  await expect(o.page.getByRole("heading", { name: "Контрагенты" })).toBeVisible();
  await expect(o.page.getByText("Контрагентов пока нет.")).toBeVisible();

  // создание
  await o.page.getByRole("button", { name: "Добавить контрагента" }).first().click();
  await fillDrawer(o.page, { name: "ООО Ромашка", tin: "123456789", customer: true });
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  const table = o.page.getByRole("table");
  await expect(table.getByRole("row", { name: /ООО Ромашка/ })).toContainText("123456789");
  await expect(table.getByRole("row", { name: /ООО Ромашка/ })).toContainText("Покупатель");

  // дубликат ИНН и неверный формат
  await o.page.getByRole("button", { name: "Добавить контрагента" }).click();
  await fillDrawer(o.page, { name: "ООО Дубль", tin: "123456789" });
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(o.page.getByText("ИНН/СТИР уже используется: «ООО Ромашка».")).toBeVisible();
  await fillDrawer(o.page, { tin: "12ab" });
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(o.page.getByText("ИНН/СТИР: только цифры, 9–14 знаков.")).toBeVisible();
  await fillDrawer(o.page, { tin: "", supplier: true });
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(table.getByRole("row", { name: /ООО Дубль/ })).toContainText("Поставщик");

  // правка
  await o.page.getByRole("button", { name: "Изменить: ООО Ромашка" }).click();
  await o.page.getByLabel("Название", { exact: true }).fill("ООО Ромашка Плюс");
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(table.getByRole("row", { name: /Ромашка Плюс/ })).toBeVisible();

  // архив с подтверждением, затем фильтр «В архиве» и возврат
  await o.page.getByRole("button", { name: "В архив: ООО Ромашка Плюс" }).click();
  await o.page.getByRole("button", { name: "Подтвердить" }).click();
  await expect(table.getByText("ООО Ромашка Плюс")).toHaveCount(0);
  await o.page.getByLabel("Статус", { exact: true }).selectOption("archived");
  await expect(o.page).toHaveURL(/status=archived/);
  const archived = o.page.getByRole("table").getByRole("row", { name: /Ромашка Плюс/ });
  await expect(archived).toContainText("В архиве");
  await expect(archived.getByRole("button", { name: /Изменить/ })).toHaveCount(0); // архивного не правят
  await o.page.getByRole("button", { name: "Вернуть: ООО Ромашка Плюс" }).click();
  await o.page.getByLabel("Статус", { exact: true }).selectOption("active");
  await expect(o.page.getByRole("table").getByRole("row", { name: /Ромашка Плюс/ })).toBeVisible();
  await o.ctx.close();
});

test("списки: поиск, сортировка, пагинация и состояние в адресной строке", async ({ browser, isMobile }) => {
  const o = await ownerWithOrg(browser);
  const cookies = await o.ctx.cookies();
  const csrf = cookies.find((c) => c.name === "hisob_csrf")!.value;
  for (let i = 1; i <= 30; i++) {
    const r = await o.page.request.post(`/api/v1/organizations/${o.orgId}/counterparties`, {
      data: { name: `Контрагент ${String(i).padStart(3, "0")}`, tin: String(100000000 + i) },
      headers: { "X-CSRF-Token": csrf },
    });
    expect(r.status()).toBe(201);
  }

  await o.page.goto(`${o.orgUrl}/counterparties`);
  const rows = o.page.getByRole("table").locator("tbody tr");
  await expect(o.page.getByText("Всего: 30")).toBeVisible();
  await expect(rows).toHaveCount(25);
  await expect(o.page.getByText("Страница 1 из 2")).toBeVisible();
  await expect(rows.first()).toContainText("Контрагент 001");

  await o.page.getByRole("button", { name: "Вперёд" }).click();
  await expect(o.page).toHaveURL(/page=2/);
  await expect(rows).toHaveCount(5);
  await o.page.reload(); // состояние восстанавливается из URL
  await expect(o.page.getByText("Страница 2 из 2")).toBeVisible();
  await expect(rows).toHaveCount(5);

  // сортировка: повторный клик по заголовку меняет направление и сбрасывает страницу
  if (isMobile) {
    // на телефоне заголовков нет: сортировка через отдельный выбор
    await o.page.getByLabel("Порядок").selectOption("desc");
  } else {
    await o.page.getByRole("button", { name: /^Название/ }).click();
    await expect(o.page.locator("th", { hasText: "Название" })).toHaveAttribute("aria-sort", "descending");
  }
  await expect(o.page).toHaveURL(/order=desc/);
  await expect(o.page).not.toHaveURL(/page=2/);
  await expect(rows.first()).toContainText("Контрагент 030");

  // поиск (с задержкой), по ИНН и по названию
  await o.page.getByRole("searchbox", { name: "Поиск" }).fill("100000007");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Контрагент 007");
  await expect(o.page).toHaveURL(/q=100000007/);
  await o.page.getByRole("searchbox", { name: "Поиск" }).fill("нет-такого");
  await expect(o.page.getByText("Ничего не найдено. Измените поиск или фильтры.")).toBeVisible();
  await o.page.getByRole("button", { name: "Сбросить" }).click();
  await expect(o.page.getByText("Всего: 30")).toBeVisible();
  await o.ctx.close();
});

test("контрагенты на узбекском и английском: интерфейс и ошибки", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.goto(`${o.orgUrl}/counterparties`);
  await o.page.getByLabel("Язык").selectOption("uz");
  await expect(o.page.getByRole("heading", { name: "Kontragentlar" })).toBeVisible();
  await o.page.getByRole("button", { name: "Kontragent qoʻshish" }).first().click();
  await o.page.getByLabel("Nomi", { exact: true }).fill("Test MCHJ");
  await o.page.getByLabel("STIR", { exact: true }).fill("12ab");
  await o.page.getByRole("button", { name: "Saqlash" }).click();
  await expect(o.page.getByText("STIR: faqat raqamlar, 9–14 ta belgi.")).toBeVisible();
  await o.page.getByRole("button", { name: "Yopish" }).click();
  await o.page.getByLabel("Til").selectOption("en");
  await expect(o.page.getByRole("heading", { name: "Counterparties" })).toBeVisible();
  await expect(o.page.getByText("No counterparties yet.")).toBeVisible();
  await o.ctx.close();
});
