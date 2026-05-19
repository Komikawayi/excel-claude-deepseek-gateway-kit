# Excel Claude -> MiMo Gateway (Local)

## Why this exists
This gateway keeps the existing Claude/Office plugin flow (`/v1/models` + `/v1/messages`) while routing requests to a MiMo Anthropic-compatible upstream.

## Credential modes (important)
- `sk-...` key: PAYG mode, routes to `MIMO_PAYG_BASE_URL`
- `tp-...` key: Token Plan mode, routes by region to TP base URL
- `sk-` and `tp-` cannot be mixed in the same runtime/config

## TP region behavior
- Default TP region is `cn` (from `MIMO_TP_REGION=cn`)
- You can override per request with header `x-mimo-tp-region: sgp` or `x-mimo-tp-region: ams`

## What it implements
- `GET /healthz`
- `GET /v1/models`
- `POST /v1/messages` (proxy to MiMo Anthropic-compatible `/v1/messages`)

## Setup
1. Copy `.env.example` to `.env`.
2. Fill `MIMO_API_KEY`.
3. Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-gateway.ps1
```

Default local URL: `http://127.0.0.1:8789`

## Isolation and rollback
- Existing gateways remain unchanged:
  - DS/other gateway: `http://127.0.0.1:8787`
  - Kimi gateway: `http://127.0.0.1:8788`
  - MiMo gateway: `http://127.0.0.1:8789`
- Rollback is instant: switch Office plugin Gateway URL back to `8787` or `8788`.
- Starting/stopping MiMo only affects port `8789` and does not touch `8787/8788`.
