#!/bin/bash
# Выполняется один раз при первой инициализации тома PostgreSQL.
# Роли: hisob_migrator (владелец БД и таблиц) и hisob_app (роль приложения:
# не владелец, NOBYPASSRLS, поэтому политики RLS на неё действуют).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE hisob_migrator LOGIN PASSWORD '${MIGRATOR_DB_PASSWORD}' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE hisob_app      LOGIN PASSWORD '${APP_DB_PASSWORD}'      NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
ALTER DATABASE ${POSTGRES_DB} OWNER TO hisob_migrator;
SQL
