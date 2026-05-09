# Excel Claude -> DeepSeek Gateway (Local, macOS)

## Why this exists
Excel Claude Gateway mode may probe `GET /v1/models`. DeepSeek's Anthropic-compatible endpoint supports `/v1/messages`, but clients may still expect a local compatibility layer for model discovery and request sanitization.

## What it implements
- `GET /healthz`
- `GET /v1/models` (local static model list)
- `GET /models` (alias)
- `POST /v1/messages` (proxy to DeepSeek `/anthropic/v1/messages`)

## Setup
1. Copy `.env.example` to `.env`.
2. Fill `DEEPSEEK_API_KEY` if you want the gateway to use a fixed upstream key.
3. If Office requires HTTPS, generate and trust a local development certificate:

```bash
./generate-dev-cert.sh
./trust-dev-ca.sh
```

4. Run:

```bash
chmod +x ./*.sh
./run-gateway.sh
```

Default local URL: `http://127.0.0.1:8787`

If `.env` contains `ENABLE_HTTPS=1`, the gateway starts in HTTPS mode instead.

## Background mode

```bash
./start-gateway.sh
./stop-gateway.sh
```

The background script writes logs to `gateway.log` and stores the running PID in `.gateway.pid`.

## Excel fields
- Gateway URL: `https://<你的地址>:8787` when HTTPS is enabled, otherwise `http://127.0.0.1:8787`
- Token: any non-empty value if `.env` already contains `DEEPSEEK_API_KEY`; otherwise use the real DeepSeek key here

## Notes
- The gateway listens on `127.0.0.1` by default.
- Set `ENABLE_HTTPS=1` to make `run-gateway.sh` start `uvicorn` with TLS.
- By default HTTPS reads `certs/gateway.crt` and `certs/gateway.key`; you can override them with `SSL_CERT_FILE` and `SSL_KEY_FILE`.
- Default CORS now allows both `https://pivot.claude.ai` and `null`, which is useful for Office for Mac WebView preflight requests.
- If your Office build still fails the preflight check, set `ALLOWED_ORIGINS=*` in `.env`, restart the gateway, and test again.
- If Office for Mac still cannot reach `127.0.0.1`, set `GATEWAY_HOST=0.0.0.0` and use `https://<你的局域网IP>:8787` as the Gateway URL.
- If Office cannot access localhost in your environment, deploy this service to a reachable host and update both the Gateway URL and `ALLOWED_ORIGINS` accordingly.
