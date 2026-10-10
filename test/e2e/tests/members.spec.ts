import { expect, test, type Browser, type Page } from "@playwright/test";

const PASSWORD = "correct horse battery";
const uniq = () => `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;

async function register(page: Page, email: string, name = "Тест Тестов") {
  await page.getByLabel("Имя и фамилия").fill(name);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Зарегистрироваться" }).click();
}

async function ownerWithOrg(browser: Browser) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  const email = `owner-${uniq()}@example.uz`;
  await page.goto("/register");
  await register(page, email, "Владелец Иванов");
  await page.getByLabel("Название организации").fill(`ООО Команда ${uniq()}`);
  await page.getByRole("button", { name: "Создать организацию" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  return { ctx, page, email, orgUrl: page.url() };
}

async function createInvite(page: Page, orgUrl: string, email: string, role?: string) {
  await page.goto(`${orgUrl}/members`);
  await page.getByRole("button", { name: "Пригласить" }).click();
  await page.getByLabel("Email приглашаемого").fill(email);
  if (role) await page.getByLabel("Роль", { exact: true }).selectOption({ label: role });
  await page.getByRole("button", { name: "Создать приглашение" }).click();
  const link = page.getByLabel("Ссылка-приглашение");
  await expect(link).toBeVisible();
  return link.inputValue();
}

test("приглашение: ссылка -> регистрация -> принятие -> роли, смена роли и приостановка", async ({ browser }) => {
  const owner = await ownerWithOrg(browser);
  const memberEmail = `acc-${uniq()}@example.uz`;
  const link = await createInvite(owner.page, owner.orgUrl, memberEmail, "Бухгалтер");
  expect(link).toMatch(/\/invite\/.{20,}/);
  await expect(owner.page.getByText("Письмо не отправляется")).toBeVisible();

  // приглашённый: ссылка -> регистрация (возврат на приглашение) -> принять
  const mctx = await browser.newContext();
  const m = await mctx.newPage();
  await m.goto(link);
  await expect(m.getByText("Бухгалтер")).toBeVisible();
  await m.getByRole("link", { name: "Зарегистрироваться" }).click();
  await register(m, memberEmail, "Бухгалтер Петрова");
  await expect(m.getByRole("button", { name: "Принять приглашение" })).toBeVisible();
  await m.getByRole("button", { name: "Принять приглашение" }).click();
  await expect(m).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  await expect(m.getByText("Бухгалтер", { exact: true })).toBeVisible();

  // бухгалтер видит участников, но не управляет ими
  await m.getByRole("link", { name: "Участники" }).click();
  await expect(m.getByRole("table").getByText("Бухгалтер Петрова")).toBeVisible();
  await expect(m.getByRole("button", { name: "Пригласить" })).toHaveCount(0);

  // ссылка одноразовая
  const again = await mctx.newPage();
  await again.goto(link);
  await expect(again.getByText("Приглашение уже использовано.")).toBeVisible();

  // владелец меняет роль на кладовщика -> у участника пропадает пункт «Участники»
  await owner.page.goto(`${owner.orgUrl}/members`);
  await expect(owner.page.getByRole("table").getByText("Бухгалтер Петрова")).toBeVisible();
  await owner.page.getByLabel(`Роль: ${memberEmail}`).selectOption({ label: "Кладовщик" });
  await expect(owner.page.getByLabel(`Роль: ${memberEmail}`)).toHaveValue("storekeeper");
  await m.reload();
  await expect(m.getByRole("link", { name: "Участники" })).toHaveCount(0);

  // приостановка с подтверждением -> доступ к организации потерян
  await owner.page.getByRole("button", { name: "Приостановить" }).click();
  await owner.page.getByRole("button", { name: "Подтвердить" }).click();
  await expect(owner.page.getByText("Приостановлен")).toBeVisible();
  await m.reload();
  await expect(m.getByRole("heading", { name: "Организация не найдена" })).toBeVisible();

  // возврат доступа
  await owner.page.getByRole("button", { name: "Вернуть" }).click();
  await expect(owner.page.getByText("Приостановлен")).toHaveCount(0);
  await m.reload();
  // доступ вернулся: организация снова в списке, роль прежняя (кладовщик, без права видеть участников)
  await expect(m.getByRole("combobox", { name: "Организация" })).toBeVisible();
  await expect(m.getByRole("heading", { name: "Организация не найдена" })).toHaveCount(0);
  await expect(m.getByRole("heading", { name: "Нет доступа" })).toBeVisible();

  await mctx.close();
  await owner.ctx.close();
});

test("последний владелец не может понизить себя", async ({ browser }) => {
  const owner = await ownerWithOrg(browser);
  await owner.page.goto(`${owner.orgUrl}/members`);
  await owner.page.getByLabel(`Роль: ${owner.email}`).selectOption({ label: "Аудитор (только чтение)" });
  await expect(
    owner.page.getByText("В организации должен остаться хотя бы один активный владелец."),
  ).toBeVisible();
  await expect(owner.page.getByLabel(`Роль: ${owner.email}`)).toHaveValue("owner");
  await owner.ctx.close();
});

test("приглашение нельзя принять чужим аккаунтом; отозванное не работает", async ({ browser }) => {
  const owner = await ownerWithOrg(browser);
  const invitee = `inv-${uniq()}@example.uz`;
  const link = await createInvite(owner.page, owner.orgUrl, invitee, "Аудитор (только чтение)");

  const sctx = await browser.newContext();
  const s = await sctx.newPage();
  await s.goto(`/register?next=${encodeURIComponent(new URL(link).pathname)}`);
  await register(s, `other-${uniq()}@example.uz`, "Посторонний");
  await s.getByRole("button", { name: "Принять приглашение" }).click();
  await expect(s.getByText("Приглашение выдано на другой email.")).toBeVisible();

  // отзыв
  await owner.page.goto(`${owner.orgUrl}/members`);
  await expect(owner.page.getByText(invitee)).toBeVisible();
  await owner.page.getByRole("button", { name: "Отозвать" }).click();
  await owner.page.getByRole("button", { name: "Подтвердить" }).click();
  await expect(owner.page.getByText("Нет действующих приглашений.")).toBeVisible();
  await s.goto(link);
  await expect(s.getByText("Приглашение отозвано.")).toBeVisible();

  await sctx.close();
  await owner.ctx.close();
});
