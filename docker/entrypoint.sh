#!/usr/bin/env bash
set -euo pipefail

APP_PORT="${APP_PORT:-8000}"

mkdir -p "${DATA_DIR:-/app/sessions}" "${LOGS_DIR:-/app/logs}"

exec uvicorn app.main:app --host 0.0.0.0 --port "${APP_PORT}"
