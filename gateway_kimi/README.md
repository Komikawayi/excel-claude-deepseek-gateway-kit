# Kimi Gateway (Claude-Compatible, Port 8788)

This gateway keeps Claude-compatible endpoints (`/v1/models` + `/v1/messages`) for Office/plugin clients, while routing to Kimi upstreams.

## Setup
1. Copy `.env.example` to `.env`.
2. Fill `KIMI_API_KEY`.
3. Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-gateway.ps1
```

Local URL: `http://127.0.0.1:8788`

## Dual-Mode Routing
- Codingplan-first: `sk-kimi-*` tokens route to `KIMI_CODING_BASE_URL`.
- PAYG fallback: other `sk-*` tokens route to `KIMI_PAYG_BASE_URL`.
- For codingplan requests, model is forced to `kimi-for-coding`.

## Model + Input Notes
- Default aliases resolve to `MODEL_PRIMARY` / `MODEL_FAST`.
- Image input is enabled (`image` content is accepted and proxied).

## Troubleshooting
- `401`: invalid/missing token (`KIMI_API_KEY`) or upstream rejects the incoming token.
- `400`: malformed JSON/body shape, unsupported field type, or invalid model name.
- `429`: upstream rate/quotas reached; retry with backoff or switch to PAYG path.
- If coding route fails, verify token prefix and base URLs in `.env`.

## Quick Start / Stop
- `gateway_kimi\start-gateway.bat` / `gateway_kimi\stop-gateway.bat`
- Repo root: `Start-Kimi-Gateway-8788.bat` / `Stop-Kimi-Gateway-8788.bat`
