"""Сравнение провайдеров распознавания на наборе с эталоном (срез 3.3, ТЗ §14).

Провайдеры вызываются напрямую (как в боевом коде: те же входы, схема и разбор), без БД и очереди.
Запуск на сервере (ключи берутся из окружения контейнера, не печатаются):
  docker compose run --rm --no-deps -e PYTHONPATH=/srv -v $PWD/eval-out:/out -v $PWD/eval-fonts:/fonts:ro backend \
    python scripts/eval_providers.py --out /out --font /fonts/font.ttf [--dataset DIR] [--providers openai,deepseek] \
    [--price-in-per-1m 0 --price-out-per-1m 0]
Цены за токены НЕ зашиты: передайте актуальные из прайса провайдера; без них стоимость не считается.
"""

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.modules.documents.extraction import providers as P
from app.modules.documents.extraction.inputs import build_input
from app.modules.documents.extraction.parsing import (
    digits_only,
    normalize_name,
    parse_date,
    parse_decimal,
)
from app.modules.documents.extraction.schema import HEADER_FIELDS, ExtractionOut
from scripts.eval_dataset import write_dataset

LINE_EVAL = ("description", "quantity", "unit", "unit_price", "amount")
HIGH_CONF = 0.85


def norm(field_name: str, v: str | None) -> Any:
    if v is None or str(v).strip() == "":
        return None
    if field_name == "date":
        return parse_date(v)
    if field_name in (
        "total_amount",
        "tax_total",
        "quantity",
        "unit_price",
        "amount",
        "discount",
        "tax_amount",
    ):
        d = parse_decimal(v)
        return d.normalize() if isinstance(d, Decimal) else None
    if field_name in ("seller_tin", "buyer_tin"):
        return digits_only(v)
    if field_name in ("seller_name", "buyer_name", "description"):
        return normalize_name(v)
    if field_name == "doc_type":
        # «other» — это «тип неизвестен»: для документа без явного типа (таблица) оно равно null
        v2 = str(v).strip().lower()
        return None if v2 == "other" else v2
    if field_name == "number":
        return str(v).strip().upper().replace(" ", "")
    return str(v).strip().lower()


@dataclass
class Tally:
    correct: int = 0
    correct_null: int = 0
    wrong: int = 0
    missed: int = 0
    hallucinated: int = 0
    conf_ok: list[float] = field(default_factory=list)
    conf_bad: list[float] = field(default_factory=list)
    high_conf_errors: int = 0

    def add(self, exp: Any, got: Any, conf: float | None) -> str:
        if exp is None and got is None:
            self.correct_null += 1
            return "correct_null"
        if exp is None:
            self.hallucinated += 1
            self._bad(conf)
            return "hallucinated"
        if got is None:
            self.missed += 1
            return "missed"
        if exp == got:
            self.correct += 1
            if conf is not None:
                self.conf_ok.append(conf)
            return "correct"
        self.wrong += 1
        self._bad(conf)
        return "wrong"

    def _bad(self, conf: float | None) -> None:
        if conf is not None:
            self.conf_bad.append(conf)
            if conf >= HIGH_CONF:
                self.high_conf_errors += 1

    @property
    def total(self) -> int:
        return self.correct + self.correct_null + self.wrong + self.missed + self.hallucinated

    @property
    def errors(self) -> int:
        return self.wrong + self.missed + self.hallucinated


def score(out: ExtractionOut, expected: dict[str, Any], t: Tally) -> list[str]:
    """Возвращает список ошибок полей (для разбора), обновляет сводку."""
    problems: list[str] = []
    for name in HEADER_FIELDS:
        f = getattr(out, name)
        r = t.add(norm(name, expected.get(name)), norm(name, f.value), f.confidence)
        if r in ("wrong", "missed", "hallucinated"):
            problems.append(f"{name}:{r} (ждали {expected.get(name)!r}, получили {f.value!r})")
    exp_lines = expected.get("lines", [])
    for i in range(max(len(exp_lines), len(out.lines))):
        e = exp_lines[i] if i < len(exp_lines) else None
        g = out.lines[i] if i < len(out.lines) else None
        if e is None:  # лишняя строка: все её непустые поля — выдумка
            assert g is not None
            for name in LINE_EVAL:
                if getattr(g, name).value is not None:
                    t.add(None, "x", getattr(g, name).confidence)
            problems.append(f"line {i + 1}: лишняя строка")
            continue
        for name in LINE_EVAL:
            gf = getattr(g, name) if g is not None else None
            r = t.add(
                norm(name, e.get(name)),
                norm(name, gf.value if gf else None),
                gf.confidence if gf else None,
            )
            if r in ("wrong", "missed", "hallucinated"):
                problems.append(
                    f"line {i + 1}.{name}:{r} (ждали {e.get(name)!r}, получили {gf.value if gf else None!r})"
                )
    return problems


def load(dataset: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = json.loads((dataset / "manifest.json").read_text())
    return items


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}%" if b else "—"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="eval-out")
    ap.add_argument(
        "--dataset",
        default=None,
        help="каталог с manifest.json; по умолчанию генерируется синтетический",
    )
    ap.add_argument("--font", default=None, help="TTF с кириллицей для синтетических изображений")
    ap.add_argument("--providers", default="openai,deepseek")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--price-in-per-1m", type=float, default=None)
    ap.add_argument("--price-out-per-1m", type=float, default=None)
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.dataset:
        dataset = Path(args.dataset)
        synthetic = False
    else:
        dataset = out_dir / "dataset"
        write_dataset(dataset, args.font)
        synthetic = True
    items = load(dataset)
    print(
        f"набор: {dataset} ({'СИНТЕТИЧЕСКИЙ' if synthetic else 'внешний'}), документов: {len(items)}"
    )

    results: dict[str, Any] = {}
    for pname in [p.strip() for p in args.providers.split(",") if p.strip()]:
        prov = P._build(pname)
        if not prov.configured():
            print(f"[{pname}] не настроен — пропуск")
            continue
        t = Tally()
        docs_with_edit = docs_ok = 0
        per_doc: list[dict[str, Any]] = []
        lat: list[float] = []
        tin = tout = 0
        unsupported = failed = 0
        for it in items:
            data = (dataset / it["file"]).read_bytes()
            inp = build_input(it["content_type"], data, it["file"])
            for rep in range(args.repeat):
                t0 = time.monotonic()
                row: dict[str, Any] = {"name": it["name"], "tags": it["tags"], "rep": rep}
                try:
                    res = prov.extract(inp)
                except P.ProviderError as exc:
                    if exc.code == "provider_unsupported_input":
                        unsupported += 1
                        row["status"] = "unsupported"
                    else:
                        failed += 1
                        row["status"] = f"error:{exc.code}"
                    per_doc.append(row)
                    continue
                dt = time.monotonic() - t0
                lat.append(dt)
                tin += res.tokens_in or 0
                tout += res.tokens_out or 0
                try:
                    out = ExtractionOut.model_validate(res.payload)
                except ValidationError:
                    failed += 1
                    row["status"] = "error:schema"
                    per_doc.append(row)
                    continue
                problems = score(out, it["expected"], t)
                docs_ok += 1
                docs_with_edit += 1 if problems else 0
                row.update(status="ok", seconds=round(dt, 1), problems=problems)
                per_doc.append(row)
                print(
                    f"[{pname}] {it['name']}: {'OK' if not problems else 'ошибок: ' + str(len(problems))} ({dt:.1f} с)"
                )
        summary: dict[str, Any] = {
            "provider": prov.name,
            "model": prov.model,
            "documents_scored": docs_ok,
            "unsupported_input": unsupported,
            "failed": failed,
            "fields_total": t.total,
            "field_accuracy": pct(t.correct + t.correct_null, t.total),
            "manual_edit_rate_fields": pct(t.errors, t.total),
            "docs_needing_edit": f"{docs_with_edit}/{docs_ok}",
            "wrong": t.wrong,
            "missed": t.missed,
            "hallucinated": t.hallucinated,
            "high_conf_errors": t.high_conf_errors,
            "avg_conf_correct": round(statistics.mean(t.conf_ok), 3) if t.conf_ok else None,
            "avg_conf_wrong": round(statistics.mean(t.conf_bad), 3) if t.conf_bad else None,
            "latency_p50_s": round(statistics.median(lat), 1) if lat else None,
            "latency_max_s": round(max(lat), 1) if lat else None,
            "tokens_in": tin,
            "tokens_out": tout,
            "tokens_per_doc": round((tin + tout) / docs_ok) if docs_ok else None,
        }
        if args.price_in_per_1m is not None and args.price_out_per_1m is not None and docs_ok:
            cost = tin / 1e6 * args.price_in_per_1m + tout / 1e6 * args.price_out_per_1m
            summary["cost_per_doc"] = round(cost / docs_ok, 5)
        results[pname] = {"summary": summary, "documents": per_doc}

    (out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print("\n=== СВОДКА ===")
    for r in results.values():
        print(json.dumps(r["summary"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
