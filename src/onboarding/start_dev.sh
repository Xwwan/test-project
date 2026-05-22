#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

export APP_DB_PATH="${APP_DB_PATH:-data/app.db}"
export APP_DATA_DIR="${APP_DATA_DIR:-data}"

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "DEEPSEEK_API_KEY is required for DeepSeek official API." >&2
  echo "Set it in .env or export it before running this script." >&2
  exit 1
fi

exec python3 -m src.onboarding.dev_server
