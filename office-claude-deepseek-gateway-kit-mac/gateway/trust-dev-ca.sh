#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CA_CERT="${SSL_CA_CERT_FILE:-$SCRIPT_DIR/certs/dev-ca.crt}"
KEYCHAIN_PATH="${KEYCHAIN_PATH:-$HOME/Library/Keychains/login.keychain-db}"

if [ ! -f "$CA_CERT" ]; then
  echo "未找到 CA 证书: $CA_CERT" >&2
  echo "请先运行 ./generate-dev-cert.sh" >&2
  exit 1
fi

security add-trusted-cert -d -r trustRoot -k "$KEYCHAIN_PATH" "$CA_CERT"

echo "已将开发 CA 导入登录钥匙串:"
echo "- $CA_CERT"
echo
echo "如果系统弹出信任确认，请选择始终信任。"
