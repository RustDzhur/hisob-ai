#!/usr/bin/env bash
# Проверка восстановления: копия разворачивается во ВРЕМЕННУЮ БД и временный каталог (рабочая БД не затрагивается),
# сверяются контрольные суммы и числа строк, проверяются бухгалтерские и складские инварианты.
# Запуск из корня проекта:  infra/backup/verify-restore.sh <каталог_копии>
set -euo pipefail

SRC="${1:?укажите каталог копии, например backups/20261011T120000Z}"
TMP_DB="hisob_restore_check"
compose() { docker compose "$@"; }
DB_USER="$(compose exec -T postgres printenv POSTGRES_USER)"
DB_NAME="$(compose exec -T postgres printenv POSTGRES_DB)"
psql_admin() { compose exec -T postgres psql -U "$DB_USER" -v ON_ERROR_STOP=1 -Atq "$@"; }

echo "[1/5] Контрольные суммы"
(cd "$SRC" && sha256sum -c <(grep -E '^[0-9a-f]{64}  ' MANIFEST.txt))

echo "[2/5] Восстановление БД в $TMP_DB"
psql_admin -d postgres -c "DROP DATABASE IF EXISTS $TMP_DB" -c "CREATE DATABASE $TMP_DB"
compose exec -T postgres pg_restore -U "$DB_USER" -d "$TMP_DB" --no-password --exit-on-error < "$SRC/db.dump"

echo "[3/5] Сравнение чисел строк с рабочей БД"
TABLES="$(psql_admin -d "$TMP_DB" -c "select tablename from pg_tables where schemaname='public' order by 1")"
FAIL=0
for t in $TABLES; do
  a="$(psql_admin -d "$DB_NAME" -c "select count(*) from $t")"
  b="$(psql_admin -d "$TMP_DB" -c "select count(*) from $t")"
  if [ "$a" != "$b" ]; then echo "  расхождение: $t рабочая=$a копия=$b (допустимо, если после копии вносили данные)"; FAIL=1; fi
done
[ "$FAIL" = 0 ] && echo "  числа строк совпадают во всех таблицах"

echo "[4/5] Инварианты на восстановленной БД"
psql_admin -d "$TMP_DB" <<'SQL'
\set ON_ERROR_STOP on
DO $$
DECLARE bad int;
BEGIN
  -- каждая проводка сбалансирована: дебет = кредит
  SELECT count(*) INTO bad FROM (
    SELECT entry_id FROM journal_line GROUP BY entry_id HAVING sum(debit) <> sum(credit)) x;
  IF bad > 0 THEN RAISE EXCEPTION 'несбалансированных проводок: %', bad; END IF;
  -- остаток на складе = сумма движений
  SELECT count(*) INTO bad FROM (
    SELECT b.product_id FROM stock_balance b
    LEFT JOIN (SELECT organization_id, warehouse_id, product_id, sum(quantity_delta) q FROM stock_movement GROUP BY 1,2,3) m
      ON m.organization_id=b.organization_id AND m.warehouse_id=b.warehouse_id AND m.product_id=b.product_id
    WHERE b.quantity <> coalesce(m.q,0)) y;
  IF bad > 0 THEN RAISE EXCEPTION 'остатков, не равных сумме движений: %', bad; END IF;
  -- сопоставления банка не превышают сумму операции
  SELECT count(*) INTO bad FROM (
    SELECT t.id FROM bank_transaction t
    JOIN bank_match m ON m.transaction_id=t.id AND m.status='CONFIRMED'
    GROUP BY t.id, t.amount HAVING sum(m.amount) > abs(t.amount)) z;
  IF bad > 0 THEN RAISE EXCEPTION 'сопоставлений сверх суммы операции: %', bad; END IF;
END $$;
SELECT 'инварианты выполнены; версия миграций ' || version_num FROM alembic_version;
SQL

echo "[5/5] Файлы документов"
TMPDIR_FILES="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_FILES"; psql_admin -d postgres -c "DROP DATABASE IF EXISTS $TMP_DB" >/dev/null || true' EXIT
tar xzf "$SRC/storage.tar.gz" -C "$TMPDIR_FILES"
echo "  файлов в копии хранилища: $(find "$TMPDIR_FILES" -type f | wc -l)"
echo "ВОССТАНОВЛЕНИЕ ПРОВЕРЕНО: $SRC"
