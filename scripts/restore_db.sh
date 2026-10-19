#!/usr/bin/env bash
# scripts/restore_db.sh
# ──────────────────────────────────────────────────────────────────────────────
# Restaura un respaldo (creado con scripts/backup_db.sh) en un Postgres LOCAL.
#
# Guard de seguridad: este script solo acepta LOCAL_DB_URL apuntando a
# localhost/127.0.0.1 — nunca corre contra producción por accidente.
#
# Uso:
#   createdb medsync_restore   # la base destino debe existir
#   LOCAL_DB_URL="postgresql://usuario@localhost:5432/medsync_restore" \
#     ./scripts/restore_db.sh backups/medsync_20261019_030000.dump
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Uso: $0 <archivo .dump>" >&2
  exit 1
fi

DUMP_FILE="$1"
if [ ! -f "$DUMP_FILE" ]; then
  echo "ERROR: no existe el archivo '$DUMP_FILE'." >&2
  exit 1
fi

if [ -z "${LOCAL_DB_URL:-}" ]; then
  echo "ERROR: define LOCAL_DB_URL (debe apuntar a localhost/127.0.0.1)." >&2
  exit 1
fi

case "$LOCAL_DB_URL" in
  *localhost*|*127.0.0.1*) ;;
  *)
    echo "ERROR: LOCAL_DB_URL debe apuntar a localhost/127.0.0.1 — este script nunca debe correr contra producción." >&2
    exit 1
    ;;
esac

echo "Restaurando ${DUMP_FILE} en ${LOCAL_DB_URL} ..."
pg_restore \
  --dbname="$LOCAL_DB_URL" \
  --no-owner \
  --no-privileges \
  --clean \
  --if-exists \
  "$DUMP_FILE"

echo "✓ Restauración completa."
