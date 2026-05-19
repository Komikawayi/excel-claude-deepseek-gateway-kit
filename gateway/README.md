# Excel Claude -> DeepSeek Gateway (Local)

## Why this exists
Excel Claude Gateway mode may probe `GET /v1/models`. DeepSeek Anthropic-compatible endpoint supports `/v1/messages`, but may not expose `/v1/models` in the required shape. This local gateway fills that gap.

## What it implements
- `GET /healthz`
- `GET /v1/models` (local static model list)
- `POST /v1/messages` (proxy to DeepSeek `/anthropic/v1/messages`)

## Setup
1. Copy `.env.example` to `.env`.
2. Fill `DEEPSEEK_API_KEY`.
3. Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-gateway.ps1
```

Default local URL: `http://127.0.0.1:8787`

## Excel fields
- Gateway URL: `http://127.0.0.1:8787`
- Token: any non-empty value (currently ignored by local gateway auth)

Note: If Excel cannot access localhost from its webview policy in your environment, deploy this service to a LAN/server URL and keep CORS aligned with `https://pivot.claude.ai`.
