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
  await page.getByLabel("Название организации").fill(`ООО Товары ${uniq()}`);
  await page.getByRole("button", { name: "Создать организацию" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  return { ctx, page, orgUrl: page.url() };
}

async function fill(page: Page, v: { sku?: string; name?: string; price?: string; barcodes?: string; kind?: string }) {
  if (v.kind) await page.getByLabel("Тип", { exact: true }).last().selectOption({ label: v.kind });
  if (v.sku !== undefined) await page.getByLabel("Артикул (SKU)").fill(v.sku);
  if (v.name !== undefined) await page.getByLabel("Название", { exact: true }).fill(v.name);
  if (v.price !== undefined) await page.getByLabel("Цена", { exact: true }).fill(v.price);
  if (v.barcodes !== undefined) await page.getByLabel("Штрихкоды").fill(v.barcodes);
}

test("товары: создание с ценой и штрихкодами, ошибки, правка, архив", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.getByRole("link", { name: "Товары и услуги" }).click();
  await expect(o.page.getByText("Товаров и услуг пока нет.")).toBeVisible();

  await o.page.getByRole("button", { name: "Добавить товар или услугу" }).first().click();
  await fill(o.page, { sku: "MILK-1", name: "Молоко 3%", price: "1 500,5", barcodes: "4780001230001\nMILK-CODE" });
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  const table = o.page.getByRole("table");
  const row = table.getByRole("row", { name: /MILK-1/ });
  await expect(row).toContainText("Молоко 3%");
  await expect(row).toContainText(/1\s500,50\s?UZS/); // запятая принята, сумма показана с 2 знаками
  await expect(row).toContainText("4780001230001, MILK-CODE");
  await expect(row).toContainText("шт");

  // ошибки: повторный артикул, чужой штрихкод, лишние знаки в цене
  await o.page.getByRole("button", { name: "Добавить товар или услугу" }).click();
  await fill(o.page, { sku: "milk-1", name: "Дубль" });
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(o.page.getByText("Артикул уже используется: «Молоко 3%».")).toBeVisible();
  await fill(o.page, { sku: "MILK-2", barcodes: "milk-code" });
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(o.page.getByText("Штрихкод MILK-CODE уже у товара MILK-1.")).toBeVisible();
  await fill(o.page, { barcodes: "", price: "10.005" });
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(o.page.getByText("Знаков после запятой не больше 2.")).toBeVisible();
  await fill(o.page, { price: "", kind: "Услуга" });
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(table.getByRole("row", { name: /MILK-2/ })).toContainText("Услуга");
  await expect(table.getByRole("row", { name: /MILK-2/ })).toContainText("—"); // без цены

  // правка цены и штрихкодов
  await o.page.getByRole("button", { name: "Изменить: MILK-1" }).click();
  await fill(o.page, { price: "2000", barcodes: "4780001230001" });
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  await expect(row).toContainText(/2\s000,00\s?UZS/);
  await expect(row).not.toContainText("MILK-CODE");

  // архив и фильтры
  await o.page.getByRole("button", { name: "В архив: MILK-1" }).click();
  await o.page.getByRole("button", { name: "Подтвердить" }).click();
  await expect(table.getByText("MILK-1")).toHaveCount(0);
  await o.page.getByLabel("Статус", { exact: true }).selectOption("archived");
  await expect(o.page.getByRole("table").getByRole("row", { name: /MILK-1/ })).toContainText("В архиве");
  await o.page.getByRole("button", { name: "Вернуть: MILK-1" }).click();
  await o.page.getByLabel("Статус", { exact: true }).selectOption("active");
  await o.page.getByLabel("Тип", { exact: true }).first().selectOption("service");
  await expect(o.page.getByRole("table").locator("tbody tr")).toHaveCount(1);
  await o.ctx.close();
});

test("поиск по штрихкоду и артикулу; валюта и единица защищены, пока ими пользуются товары", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  // включаем доллар
  await o.page.getByRole("link", { name: "Валюты" }).click();
  await o.page.getByRole("button", { name: "Включить: USD" }).click();
  await expect(o.page.getByRole("row", { name: /USD/ }).getByText("Включена")).toBeVisible();

  await o.page.getByRole("link", { name: "Товары и услуги" }).click();
  await o.page.getByRole("button", { name: "Добавить товар или услугу" }).first().click();
  await fill(o.page, { sku: "USD-1", name: "Импортный товар", price: "9.99", barcodes: "IMPORT-0001" });
  await o.page.getByLabel("Валюта цены").selectOption("USD");
  await o.page.getByRole("button", { name: "Сохранить" }).click();
  const row = o.page.getByRole("table").getByRole("row", { name: /USD-1/ });
  await expect(row).toContainText(/9\.99|9,99/);
  await expect(row).toContainText("USD");

  await o.page.getByRole("searchbox", { name: "Поиск" }).fill("import-0001");
  await expect(o.page.getByRole("table").locator("tbody tr")).toHaveCount(1);
  await expect(o.page).toHaveURL(/q=import-0001/);

  // валюту с активным товаром отключить нельзя
  await o.page.getByRole("link", { name: "Валюты" }).click();
  await o.page.getByRole("button", { name: "Отключить: USD" }).click();
  await o.page.getByRole("button", { name: "Подтвердить" }).click();
  await expect(o.page.getByText("Валюта используется в ценах активных товаров.")).toBeVisible();
  await expect(o.page.getByRole("row", { name: /USD/ }).getByText("Включена")).toBeVisible();

  // после архивации товара — можно
  await o.page.getByRole("link", { name: "Товары и услуги" }).click();
  await o.page.getByRole("button", { name: "В архив: USD-1" }).click();
  await o.page.getByRole("button", { name: "Подтвердить" }).click();
  await o.page.getByRole("link", { name: "Валюты" }).click();
  await o.page.getByRole("button", { name: "Отключить: USD" }).click();
  await o.page.getByRole("button", { name: "Подтвердить" }).click();
  await expect(o.page.getByRole("row", { name: /USD/ }).getByText("Выключена")).toBeVisible();
  await o.ctx.close();
});

test("товары на узбекском и английском: интерфейс и формат суммы", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.goto(`${o.orgUrl}/products`);
  await o.page.getByLabel("Язык").selectOption("uz");
  await expect(o.page.getByRole("heading", { name: "Tovarlar va xizmatlar" })).toBeVisible();
  await o.page.getByRole("button", { name: "Tovar yoki xizmat qoʻshish" }).first().click();
  await o.page.getByLabel("Artikul (SKU)").fill("UZ-1");
  await o.page.getByLabel("Nomi", { exact: true }).fill("Non");
  await o.page.getByLabel("Narxi", { exact: true }).fill("12500");
  await o.page.getByRole("button", { name: "Saqlash" }).click();
  await expect(o.page.getByRole("table").getByRole("row", { name: /UZ-1/ })).toContainText(/12\s500,00\s?UZS/);
  await o.page.getByLabel("Til").selectOption("en");
  await expect(o.page.getByRole("heading", { name: "Products and services" })).toBeVisible();
  await expect(o.page.getByRole("table").getByRole("row", { name: /UZ-1/ })).toContainText(/12,500\.00\s?UZS/);
  await o.ctx.close();
});
