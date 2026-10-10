"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useT } from "@/i18n/LocaleProvider";

type Props<T> = {
  label: string;
  value: { id: string; label: string } | null;
  onChange: (item: { id: string; label: string } | null, raw: T | null) => void;
  search: (q: string) => Promise<T[]>;
  toItem: (raw: T) => { id: string; label: string; hint?: string };
  disabled?: boolean;
  error?: string;
  hideLabel?: boolean;
};

/** Выбор записи справочника с серверным поиском (без загрузки всего справочника). */
export function EntityPicker<T>({ label, value, onChange, search, toItem, disabled, error, hideLabel }: Props<T>) {
  const t = useT();
  const id = useId();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<T[] | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    if (!open) return;
    if (timer.current) clearTimeout(timer.current);
    const n = ++seq.current;
    timer.current = setTimeout(async () => {
      try {
        const res = await search(q);
        if (n === seq.current) setItems(res); // отбрасываем устаревшие ответы
      } catch {
        if (n === seq.current) setItems([]);
      }
    }, 250);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, open]);

  return (
    <div className="field picker">
      <label htmlFor={id} className={hideLabel ? "sr-only" : undefined}>{label}</label>
      <div className="picker-box">
        <input
          id={id}
          role="combobox"
          aria-expanded={open}
          aria-controls={`${id}-list`}
          aria-autocomplete="list"
          aria-invalid={error ? true : undefined}
          disabled={disabled}
          autoComplete="off"
          placeholder={t.documents.pickerSearch}
          value={open ? q : (value?.label ?? "")}
          onFocus={() => { setQ(""); setOpen(true); }}
          onChange={(e) => setQ(e.target.value)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}
        />
        {value && !disabled ? (
          <button type="button" className="picker-clear" aria-label={`${t.documents.pickerClear}: ${label}`} onClick={() => onChange(null, null)}>✕</button>
        ) : null}
      </div>
      {open ? (
        <ul id={`${id}-list`} role="listbox" className="picker-list">
          {items === null ? <li className="picker-empty">{t.common.loading}</li> : null}
          {items?.length === 0 ? <li className="picker-empty">{t.documents.pickerNone}</li> : null}
          {items?.map((raw) => {
            const it = toItem(raw);
            return (
              <li key={it.id} role="option" aria-selected={value?.id === it.id}
                onMouseDown={(e) => { e.preventDefault(); onChange({ id: it.id, label: it.label }, raw); setOpen(false); }}>
                {it.label}{it.hint ? <span className="sub"> · {it.hint}</span> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      {error ? <p className="field-error" role="alert">{error}</p> : null}
    </div>
  );
}
