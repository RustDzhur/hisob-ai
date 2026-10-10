import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthForm, validateAuth } from "./AuthForm";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams("next=%2Finvite%2Fabc"),
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ reload: vi.fn() }) }));

afterEach(() => vi.unstubAllGlobals());

describe("validateAuth", () => {
  it("логин: требует email и пароль", () => {
    expect(validateAuth("login", { email: "", password: "", fullName: "" })).toEqual({
      email: "Обязательное поле.",
      password: "Обязательное поле.",
    });
  });
  it("регистрация: короткий пароль и пустое имя", () => {
    const e = validateAuth("register", { email: "a@b.uz", password: "short", fullName: " " });
    expect(e.password).toMatch(/10 символов/);
    expect(e.full_name).toBeDefined();
  });
});

describe("<AuthForm mode=login>", () => {
  it("показывает ошибку сервера и не теряет введённый email", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ code: "invalid_credentials", message: "Неверный email или пароль." }), { status: 401 }),
      ),
    );
    const user = userEvent.setup();
    render(<AuthForm mode="login" />);
    await user.type(screen.getByLabelText("Email"), "a@b.uz");
    await user.type(screen.getByLabelText("Пароль"), "whatever-pass");
    await user.click(screen.getByRole("button", { name: "Войти" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Неверный email или пароль."));
    expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("a@b.uz");
    expect((screen.getByRole("button", { name: "Войти" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("ссылка на регистрацию сохраняет безопасный next", () => {
    render(<AuthForm mode="login" />);
    const link = screen.getByRole("link", { name: "Зарегистрироваться" });
    expect(link.getAttribute("href")).toBe("/register?next=%2Finvite%2Fabc");
  });

  it("не отправляет запрос при пустой форме и подсвечивает поля", async () => {
    const fn = vi.fn();
    vi.stubGlobal("fetch", fn);
    const user = userEvent.setup();
    render(<AuthForm mode="login" />);
    await user.click(screen.getByRole("button", { name: "Войти" }));
    expect(fn).not.toHaveBeenCalled();
    expect(screen.getAllByRole("alert")).toHaveLength(2);
  });
});
