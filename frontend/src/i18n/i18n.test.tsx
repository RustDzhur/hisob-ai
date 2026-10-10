import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { ApiError } from "@/lib/api";
import { errorText, fieldErrorMap } from "./errors";
import { DEFAULT_LOCALE, dictionaries, LOCALES } from "./index";
import { LocaleProvider, useT } from "./LocaleProvider";

type Tree = { [k: string]: string | Tree };
const flatten = (o: Tree, prefix = ""): string[] =>
  Object.entries(o).flatMap(([k, v]) => (typeof v === "string" ? [`${prefix}${k}`] : flatten(v, `${prefix}${k}.`)));

describe("словари", () => {
  it("язык по умолчанию — узбекский", () => {
    expect(DEFAULT_LOCALE).toBe("uz");
    expect(LOCALES).toEqual(["uz", "ru", "en"]);
  });

  it("во всех языках одинаковый набор ключей и нет пустых строк", () => {
    const base = flatten(dictionaries.ru as unknown as Tree).sort();
    for (const l of LOCALES) {
      const d = dictionaries[l] as unknown as Tree;
      expect(flatten(d).sort(), l).toEqual(base);
      const values = JSON.stringify(d);
      expect(values.includes('""'), `${l}: пустая строка`).toBe(false);
    }
  });

  it("плейсхолдеры {min}/{max} сохранены в каждом переводе", () => {
    for (const l of LOCALES) {
      expect(dictionaries[l].fieldErrors.password_too_short).toContain("{min}");
      expect(dictionaries[l].fieldErrors.password_too_long).toContain("{max}");
    }
  });

  it("каждый код ошибки сервера, известный бэкенду, переведён на всех языках", () => {
    const codes = ["invalid_credentials", "email_taken", "last_owner", "forbidden", "too_many_attempts", "invitation_expired", "network"];
    for (const l of LOCALES) for (const c of codes) expect(dictionaries[l].errors[c], `${l}.${c}`).toBeTruthy();
  });
});

describe("ошибки API", () => {
  const err = new ApiError(422, "weak_password", "серверный текст", [
    { field: "password", code: "password_too_short", message: "Минимум 10 символов.", params: { min: 10 } },
    { field: "x", code: "unknown_code", message: "запасной текст" },
  ]);
  it("переводятся по code, параметры подставляются", () => {
    expect(fieldErrorMap(dictionaries.uz, err).password).toBe("Kamida 10 ta belgi.");
    expect(fieldErrorMap(dictionaries.en, err).password).toBe("At least 10 characters.");
    expect(errorText(dictionaries.en, err)).toBe("The password does not meet the requirements.");
  });
  it("неизвестный code показывает сообщение сервера", () => {
    expect(fieldErrorMap(dictionaries.ru, err).x).toBe("запасной текст");
    expect(errorText(dictionaries.ru, new ApiError(500, "brand_new_code", "сообщение сервера"))).toBe("сообщение сервера");
  });
});

function Title() {
  return <h1>{useT().auth.loginTitle}</h1>;
}

describe("переключатель языка", () => {
  it("меняет язык, пишет cookie и атрибут lang", async () => {
    const user = userEvent.setup();
    render(
      <LocaleProvider initial="uz">
        <LanguageSwitcher />
        <Title />
      </LocaleProvider>,
    );
    expect(screen.getByRole("heading").textContent).toBe("Hisob AI ga kirish");
    await user.selectOptions(screen.getByRole("combobox"), "en");
    expect(screen.getByRole("heading").textContent).toBe("Sign in to Hisob AI");
    expect(document.documentElement.lang).toBe("en");
    expect(document.cookie).toContain("hisob_lang=en");
    await user.selectOptions(screen.getByRole("combobox"), "ru");
    expect(screen.getByRole("heading").textContent).toBe("Вход в Hisob AI");
    vi.restoreAllMocks();
  });
});
