#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PID_FILE="$SCRIPT_DIR/.gateway.pid"
PORT="${GATEWAY_PORT:-8787}"

stop_pid() {
  local pid="$1"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    echo "已停止 Gateway，PID=${pid}"
    return 0
  fi
  return 1
}

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE")"
  if stop_pid "$PID"; then
    rm -f "$PID_FILE"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

PORT_PID="$(lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
if stop_pid "$PORT_PID"; then
  exit 0
fi

echo '未发现正在运行的 Gateway。'
