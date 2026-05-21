#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f ".env" && -f ".env.example" ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Please fill API keys first."
fi

if [[ ! -d ".venv" ]]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -e ".[dev]" >/dev/null

set -a
if [[ -f ".env" ]]; then
  # shellcheck disable=SC1091
  source .env
fi
set +a

PORT="${GATEWAY_PORT:-8890}"
echo "Starting macOS gateway on port ${PORT} ..."
exec .venv/bin/python -m uvicorn claude_gateway_macos.main:app --host 127.0.0.1 --port "${PORT}"
