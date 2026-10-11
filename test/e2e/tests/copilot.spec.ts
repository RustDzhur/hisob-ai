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
  await page.getByLabel("Название организации").fill(`ООО Пом ${uniq()}`);
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

const CONV = "11111111-1111-4111-8111-111111111111";
const stamp = "2026-10-11T10:00:00Z";
const msg = (over: Record<string, unknown>) => ({ tool_trace: [], provider: null, model: null, mock: false, error_code: null, created_at: stamp, completed_at: null, ...over });

test("помощник: вопрос, ожидание, ответ с источниками (сеть подменена, провайдер не вызывается)", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  let polls = 0;
  await o.page.route("**/copilot/status", (r) => r.fulfill({ json: { configured: true, provider: "mock", model: "mock", mock: true } }));
  await o.page.route("**/copilot/ask", (r) => r.fulfill({ status: 202, json: { conversation_id: CONV, message: msg({ id: "a1", role: "assistant", status: "PENDING", content: "" }) } }));
  await o.page.route(`**/copilot/conversations/${CONV}`, (r) => {
    polls += 1;
    const user = msg({ id: "u1", role: "user", status: "DONE", content: "Какие документы не оплачены?", completed_at: stamp });
    const answer = polls < 2
      ? msg({ id: "a1", role: "assistant", status: "PENDING", content: "" })
      : msg({
          id: "a1", role: "assistant", status: "DONE", content: "Не оплачен один документ СФ-1 на 600 UZS [unpaid_documents].", provider: "mock", model: "mock", mock: true, completed_at: stamp,
          tool_trace: [{ tool: "unpaid_documents", args: { min_age_days: 0 }, rows: 1, truncated: false }, { tool: "bank_summary", args: {}, error: "forbidden" }],
        });
    return r.fulfill({ json: { id: CONV, title: "Какие документы не оплачены?", updated_at: stamp, messages: [user, answer] } });
  });
  await o.page.route("**/copilot/conversations", (r) => r.fulfill({ json: [{ id: CONV, title: "Какие документы не оплачены?", updated_at: stamp }] }));

  await o.page.getByRole("link", { name: "Помощник", exact: true }).click();
  await expect(o.page.getByText("Помощник только читает данные и ничего не меняет в учёте.")).toBeVisible();
  await expect(o.page.getByTestId("copilot-status")).toContainText("ТЕСТОВЫЙ РЕЖИМ");
  await o.page.getByLabel("Например: какие документы не оплачены?").fill("Какие документы не оплачены?");
  await o.page.getByRole("button", { name: "Спросить" }).click();
  await expect(o.page.getByText("Думаю…")).toBeVisible();
  const box = o.page.getByTestId("copilot-messages");
  await expect(box).toContainText("Не оплачен один документ СФ-1 на 600 UZS", { timeout: 15000 });
  await box.getByText("Источники (2)").click();
  await expect(box).toContainText("Неоплаченные документы");
  await expect(box).toContainText("Банк: сводка · нет доступа");
  await o.ctx.close();
});

test("помощник: провайдер не настроен: поле отключено, языки", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.route("**/copilot/status", (r) => r.fulfill({ json: { configured: false, provider: "openai", model: null, mock: false } }));
  await o.page.goto(`${o.orgUrl}/copilot`);
  await expect(o.page.getByText("AI-провайдер не настроен: задайте ключ")).toBeVisible();
  await expect(o.page.getByLabel("Например: какие документы не оплачены?")).toBeDisabled();
  await o.page.getByLabel("Язык").selectOption("uz");
  await expect(o.page.getByRole("heading", { name: "AI yordamchi" })).toBeVisible();
  await expect(o.page.getByText(/AI provayderi sozlanmagan/)).toBeVisible();
  await o.page.getByLabel("Til").selectOption("en");
  await expect(o.page.getByRole("heading", { name: "AI assistant" })).toBeVisible();
  await expect(o.page.getByText(/No AI provider is configured/)).toBeVisible();
  await o.ctx.close();
});

test("помощник: настоящий сервер — раздел доступен владельцу и аудитору", async ({ browser }) => {
  const o = await ownerWithOrg(browser);
  await o.page.goto(`${o.orgUrl}/copilot`);
  await expect(o.page.getByRole("heading", { name: "AI-помощник" })).toBeVisible();
  const au = await memberWithRole(browser, o, "Аудитор (только чтение)", "Аудитор Петров");
  await au.page.goto(`${o.orgUrl}/copilot`);
  await expect(au.page.getByRole("heading", { name: "AI-помощник" })).toBeVisible();
  await expect(au.page.getByTestId("copilot-messages")).toContainText("Задайте вопрос");
  await au.ctx.close();
  await o.ctx.close();
});
