#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CERT_DIR="$SCRIPT_DIR/certs"
mkdir -p "$CERT_DIR"

if ! command -v openssl >/dev/null 2>&1; then
  echo '未找到 openssl，无法生成 HTTPS 证书。' >&2
  exit 1
fi

PRIMARY_IP="${GATEWAY_CERT_PRIMARY_IP:-}"
if [ -z "$PRIMARY_IP" ]; then
  PRIMARY_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
fi
if [ -z "$PRIMARY_IP" ]; then
  PRIMARY_IP="$(ipconfig getifaddr en1 2>/dev/null || true)"
fi

HOSTS_CSV="${GATEWAY_CERT_HOSTS:-127.0.0.1,localhost}"
if [ -n "$PRIMARY_IP" ]; then
  HOSTS_CSV="${HOSTS_CSV},${PRIMARY_IP}"
fi

IFS=',' read -r -a RAW_HOSTS <<< "$HOSTS_CSV"
SAN_CONFIG=""
DNS_INDEX=1
IP_INDEX=1
CN=""

for raw_host in "${RAW_HOSTS[@]}"; do
  host="$(echo "$raw_host" | xargs)"
  if [ -z "$host" ]; then
    continue
  fi
  if [ -z "$CN" ]; then
    CN="$host"
  fi
  if [[ "$host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    SAN_CONFIG="${SAN_CONFIG}IP.${IP_INDEX} = ${host}"$'\n'
    IP_INDEX=$((IP_INDEX + 1))
  else
    SAN_CONFIG="${SAN_CONFIG}DNS.${DNS_INDEX} = ${host}"$'\n'
    DNS_INDEX=$((DNS_INDEX + 1))
  fi
done

if [ -z "$CN" ] || [ -z "$SAN_CONFIG" ]; then
  echo '未生成有效的证书主机名列表。' >&2
  exit 1
fi

CA_KEY="$CERT_DIR/dev-ca.key"
CA_CERT="$CERT_DIR/dev-ca.crt"
SERVER_KEY="$CERT_DIR/gateway.key"
SERVER_CSR="$CERT_DIR/gateway.csr"
SERVER_CERT="$CERT_DIR/gateway.crt"
OPENSSL_CNF="$CERT_DIR/openssl-gateway.cnf"

if [ ! -f "$CA_KEY" ] || [ ! -f "$CA_CERT" ]; then
  openssl genrsa -out "$CA_KEY" 2048
  openssl req -x509 -new -nodes -key "$CA_KEY" -sha256 -days 3650 \
    -out "$CA_CERT" -subj "/CN=Excel Claude Gateway Dev CA"
fi

cat >"$OPENSSL_CNF" <<EOF
[ req ]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = req_distinguished_name
req_extensions = v3_req

[ req_distinguished_name ]
CN = ${CN}

[ v3_req ]
subjectAltName = @alt_names
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[ alt_names ]
${SAN_CONFIG}
EOF

openssl genrsa -out "$SERVER_KEY" 2048
openssl req -new -key "$SERVER_KEY" -out "$SERVER_CSR" -config "$OPENSSL_CNF"
openssl x509 -req -in "$SERVER_CSR" -CA "$CA_CERT" -CAkey "$CA_KEY" -CAcreateserial \
  -out "$SERVER_CERT" -days 825 -sha256 -extensions v3_req -extfile "$OPENSSL_CNF"

rm -f "$SERVER_CSR"

echo "证书已生成:"
echo "- CA 证书: $CA_CERT"
echo "- 服务证书: $SERVER_CERT"
echo "- 服务私钥: $SERVER_KEY"
echo
echo "下一步:"
echo "1. 运行 ./trust-dev-ca.sh 把开发 CA 加入登录钥匙串"
echo "2. 在 .env 中设置 ENABLE_HTTPS=1"
echo "3. 重启网关后使用 https://<你的地址>:8787"
