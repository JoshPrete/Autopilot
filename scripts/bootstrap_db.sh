#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is required before running db bootstrap." >&2
  exit 2
fi

ALEMBIC_BIN="${ALEMBIC_BIN:-}"
if [[ -z "$ALEMBIC_BIN" ]]; then
  if [[ -x ".venv/bin/alembic" ]]; then
    ALEMBIC_BIN=".venv/bin/alembic"
  else
    ALEMBIC_BIN="alembic"
  fi
fi

table_count="$(psql "$DATABASE_URL" -Atqc \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'")"

if [[ "$table_count" == "0" ]]; then
  echo "Fresh database detected. Applying schema.sql..."
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f schema.sql
fi

has_alembic_version="$(psql "$DATABASE_URL" -Atqc \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'alembic_version'")"

if [[ "$has_alembic_version" == "0" ]]; then
  echo "Stamping baseline revision 0001..."
  "$ALEMBIC_BIN" stamp 0001
fi

echo "Applying Alembic migrations..."
"$ALEMBIC_BIN" upgrade head

echo "Database bootstrap complete."
