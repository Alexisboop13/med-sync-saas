#!/usr/bin/env bash
# scripts/backup_db.sh
# ──────────────────────────────────────────────────────────────────────────────
# Crea un respaldo de la base de producción (Render Postgres) en formato
# custom de pg_dump (-Fc), listo para restaurar con pg_restore / scripts/restore_db.sh.
#
# Uso:
#   RENDER_DB_URL="postgresql://usuario:password@host:5432/medsync" ./scripts/backup_db.sh
#
# RENDER_DB_URL es la "External Database URL" que Render muestra en:
#   Dashboard → tu base Postgres → Connect → External Connection
# NO es la misma variable que DATABASE_URL de la app (esa lleva el driver
# +asyncpg, que pg_dump no entiende).
#
# El respaldo se guarda en backups/medsync_<fecha>_<hora>.dump — ese
# directorio está en .gitignore, nunca se commitea.
set -euo pipefail

if [ -z "${RENDER_DB_URL:-}" ]; then
  echo "ERROR: define RENDER_DB_URL con la External Database URL de Render." >&2
  echo "  Dashboard → tu base Postgres → Connect → External Connection" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${SCRIPT_DIR}/../backups"
mkdir -p "$BACKUP_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="${BACKUP_DIR}/medsync_${TIMESTAMP}.dump"

echo "Respaldando a ${OUT_FILE} ..."
pg_dump "$RENDER_DB_URL" \
  --format=custom \
  --no-owner \
  --no-privileges \
  --file="$OUT_FILE"

echo "✓ Respaldo completo: ${OUT_FILE} ($(du -h "$OUT_FILE" | cut -f1 | tr -d ' '))"
