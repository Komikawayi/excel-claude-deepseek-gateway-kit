#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo '已从 .env.example 创建 .env，请按需填写 DEEPSEEK_API_KEY。'
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  done
fi

if [ -z "$PYTHON_BIN" ]; then
  echo '未找到可用的 Python，请先安装 Python 3。' >&2
  exit 1
fi

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi

VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -r requirements.txt

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

PORT="${GATEWAY_PORT:-8787}"
HOST="${GATEWAY_HOST:-127.0.0.1}"
SCHEME="http"
UVICORN_ARGS=(--host "$HOST" --port "$PORT")

if [ "${ENABLE_HTTPS:-0}" = "1" ]; then
  CERT_FILE="${SSL_CERT_FILE:-$SCRIPT_DIR/certs/gateway.crt}"
  KEY_FILE="${SSL_KEY_FILE:-$SCRIPT_DIR/certs/gateway.key}"
  if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
    echo "未找到 HTTPS 证书，请先运行 ./generate-dev-cert.sh。" >&2
    exit 1
  fi
  SCHEME="https"
  UVICORN_ARGS+=(--ssl-certfile "$CERT_FILE" --ssl-keyfile "$KEY_FILE")
fi

echo "Gateway 启动中: ${SCHEME}://${HOST}:${PORT}"
exec "$VENV_PYTHON" -m uvicorn app.main:app "${UVICORN_ARGS[@]}"
