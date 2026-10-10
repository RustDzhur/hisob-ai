import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { dictionaries } from "@/i18n";
import { fieldErrorMap } from "@/i18n/errors";
import { LocaleProvider } from "@/i18n/LocaleProvider";
import { ApiError } from "@/lib/api";
import { DataTable, type Column } from "./DataTable";

type Row = { id: string; name: string };
const columns: Column<Row>[] = [
  { key: "name", header: "Название", sortKey: "name", render: (r) => r.name },
  { key: "x", header: "Прочее", render: () => "—" },
];
const base = {
  caption: "Список",
  columns,
  total: 60,
  page: 2,
  pageSize: 25,
  loading: false,
  error: null,
  onRetry: vi.fn(),
  sort: "name",
  order: "asc" as const,
  onSort: vi.fn(),
  onPage: vi.fn(),
  rowKey: (r: Row) => r.id,
  empty: <p>пусто</p>,
};
const wrap = (ui: React.ReactElement) => render(<LocaleProvider initial="ru">{ui}</LocaleProvider>);

describe("<DataTable>", () => {
  it("рисует строки, подписи колонок и состояние сортировки", () => {
    wrap(<DataTable {...base} rows={[{ id: "1", name: "Альфа" }, { id: "2", name: "Бета" }]} />);
    expect(screen.getByText("Альфа")).toBeTruthy();
    const th = screen.getByRole("columnheader", { name: /Название/ });
    expect(th.getAttribute("aria-sort")).toBe("ascending");
    expect(screen.getByRole("columnheader", { name: "Прочее" }).getAttribute("aria-sort")).toBeNull();
  });

  it("сортировка и страницы вызывают обработчики; границы страниц блокируют кнопки", () => {
    const onSort = vi.fn();
    const onPage = vi.fn();
    wrap(<DataTable {...base} onSort={onSort} onPage={onPage} rows={[{ id: "1", name: "A" }]} />);
    fireEvent.click(screen.getByRole("button", { name: /Название/ }));
    expect(onSort).toHaveBeenCalledWith("name");
    fireEvent.click(screen.getByRole("button", { name: "Назад" }));
    expect(onPage).toHaveBeenCalledWith(1);
    expect(screen.getByText(/Страница 2 из 3/)).toBeTruthy();
    wrap(<DataTable {...base} page={3} onPage={onPage} rows={[{ id: "1", name: "A" }]} />);
    expect((screen.getAllByRole("button", { name: "Вперёд" })[1] as HTMLButtonElement).disabled).toBe(true);
  });

  it("пустой список показывает переданное состояние, ошибка — повтор", () => {
    wrap(<DataTable {...base} rows={[]} total={0} page={1} />);
    expect(screen.getByText("пусто")).toBeTruthy();
    const retry = vi.fn();
    wrap(<DataTable {...base} rows={null} error={new ApiError(500, "internal_error", "x", [], "cid-9")} onRetry={retry} />);
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(retry).toHaveBeenCalled();
    expect(screen.getByText("cid-9")).toBeTruthy();
  });
});

describe("ошибка занятого ИНН", () => {
  const err = new ApiError(409, "counterparty_tin_taken", "x", [
    { field: "tin", code: "counterparty_tin_taken", message: "m", params: { name: "ООО Ромашка" } },
  ]);
  it("подставляет имя контрагента на всех языках", () => {
    expect(fieldErrorMap(dictionaries.ru, err).tin).toBe("ИНН/СТИР уже используется: «ООО Ромашка».");
    expect(fieldErrorMap(dictionaries.uz, err).tin).toContain("ООО Ромашка");
    expect(fieldErrorMap(dictionaries.en, err).tin).toContain("ООО Ромашка");
  });
});
