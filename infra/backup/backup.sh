#!/usr/bin/env bash
# Резервная копия: БД (pg_dump, формат custom), файлы документов (том storage-data), манифест с контрольными суммами.
# Запуск из корня проекта на сервере:  infra/backup/backup.sh [каталог_копий]   (по умолчанию ./backups)
# Секреты из .env не печатаются. Redis не копируется: это очередь, истина о задачах — в PostgreSQL.
set -euo pipefail

DEST_ROOT="${1:-./backups}"
KEEP="${BACKUP_KEEP:-14}"            # сколько последних копий хранить
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$DEST_ROOT/$STAMP"
mkdir -p "$DEST"

compose() { docker compose "$@"; }
DB_USER="$(compose exec -T postgres printenv POSTGRES_USER)"
DB_NAME="$(compose exec -T postgres printenv POSTGRES_DB)"

echo "[1/3] БД $DB_NAME -> $DEST/db.dump"
compose exec -T postgres pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc --no-password > "$DEST/db.dump"

echo "[2/3] Файлы документов -> $DEST/storage.tar.gz"
# том читается отдельным одноразовым контейнером; хранилище при этом не останавливается (копия «на лету»)
VOL="$(docker volume ls --format '{{.Name}}' | grep -E '(^|_)storage-data$' | head -1)"
docker run --rm -v "$VOL":/data:ro -v "$(cd "$DEST" && pwd)":/out alpine:3.20 \
  sh -c "tar czf /out/storage.tar.gz -C /data . && chown $(id -u):$(id -g) /out/storage.tar.gz"

echo "[3/3] Манифест"
(
  cd "$DEST"
  {
    echo "created_utc=$STAMP"
    echo "db=$DB_NAME"
    echo "alembic_head=$(compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -Atc 'select version_num from alembic_version' | tr -d '\r')"
    sha256sum db.dump storage.tar.gz
  } > MANIFEST.txt
)
chmod 600 "$DEST"/*

# ротация: оставляем KEEP последних
ls -1d "$DEST_ROOT"/*/ 2>/dev/null | sort | head -n -"$KEEP" | xargs -r rm -rf
echo "Готово: $DEST"
