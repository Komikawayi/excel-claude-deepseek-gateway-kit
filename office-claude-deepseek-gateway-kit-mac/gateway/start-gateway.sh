#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PID_FILE="$SCRIPT_DIR/.gateway.pid"
LOG_FILE="$SCRIPT_DIR/gateway.log"

if [ -f "$PID_FILE" ]; then
  EXISTING_PID="$(cat "$PID_FILE")"
  if [ -n "$EXISTING_PID" ] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    echo "Gateway 已在运行，PID=${EXISTING_PID}"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

nohup "$SCRIPT_DIR/run-gateway.sh" >>"$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"

sleep 2
if kill -0 "$PID" 2>/dev/null; then
  echo "Gateway 已后台启动，PID=${PID}"
  echo "日志文件: $LOG_FILE"
  exit 0
fi

echo 'Gateway 启动失败，请检查 gateway.log。' >&2
exit 1
