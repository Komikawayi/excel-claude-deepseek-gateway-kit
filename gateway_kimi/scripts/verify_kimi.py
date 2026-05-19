import json
import os
import time
from typing import Any, Dict, List

import httpx


VERIFY_KIMI_BASE = os.getenv("VERIFY_KIMI_BASE", "http://127.0.0.1:8788").rstrip("/")
VERIFY_KIMI_KEY = os.getenv("VERIFY_KIMI_KEY", "").strip()
VERIFY_KIMI_IMAGE_DATA_URL = os.getenv("VERIFY_KIMI_IMAGE_DATA_URL", "").strip()
VERIFY_KIMI_ROUTE_OVERRIDE = os.getenv("VERIFY_KIMI_ROUTE_OVERRIDE", "").strip().lower()


def _headers(api_key: str) -> Dict[str, str]:
    return {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def _pick_key_for_mode(mode: str) -> str:
    if VERIFY_KIMI_ROUTE_OVERRIDE in {"codingplan", "payg"}:
        if not VERIFY_KIMI_KEY:
            raise RuntimeError("VERIFY_KIMI_KEY is required when VERIFY_KIMI_ROUTE_OVERRIDE is set.")
        return VERIFY_KIMI_KEY

    if not VERIFY_KIMI_KEY:
        raise RuntimeError("VERIFY_KIMI_KEY is required for auth scenarios.")

    if mode == "codingplan":
        if VERIFY_KIMI_KEY.lower().startswith("sk-kimi-"):
            return VERIFY_KIMI_KEY
        return "sk-kimi-worker-codingplan-smoke"

    if VERIFY_KIMI_KEY.lower().startswith("sk-kimi-"):
        return "sk-worker-payg-smoke"
    return VERIFY_KIMI_KEY


def _mode_for_key(api_key: str) -> str:
    if VERIFY_KIMI_ROUTE_OVERRIDE in {"codingplan", "payg"}:
        return VERIFY_KIMI_ROUTE_OVERRIDE
    return "codingplan" if api_key.lower().startswith("sk-kimi-") else "payg"


def _preview_payload(payload: Any, limit: int = 320) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False)[:limit]
    except Exception:
        return str(payload)[:limit]


def check_health() -> Dict[str, Any]:
    r = httpx.get(f"{VERIFY_KIMI_BASE}/healthz", timeout=10)
    return {"name": "healthz", "status": r.status_code, "body": r.text[:200]}


def check_models(api_key: str) -> Dict[str, Any]:
    r = httpx.get(f"{VERIFY_KIMI_BASE}/v1/models", headers={"x-api-key": api_key}, timeout=15)
    ok = False
    preview: Any
    try:
        payload = r.json()
        ok = isinstance(payload, dict) and isinstance(payload.get("data"), list) and len(payload["data"]) > 0
        preview = payload
    except Exception:
        preview = r.text[:300]
    return {"name": "models", "status": r.status_code, "ok": ok, "preview": _preview_payload(preview, 300)}


def check_non_stream(api_key: str, mode: str) -> Dict[str, Any]:
    model = "claude-sonnet-4-5" if mode == "payg" else "kimi-for-coding"
    body = {
        "model": model,
        "max_tokens": 96,
        "stream": False,
        "messages": [{"role": "user", "content": f"请用中文回复：{mode} 非流式检查通过"}],
    }
    t0 = time.time()
    r = httpx.post(f"{VERIFY_KIMI_BASE}/v1/messages", headers=_headers(api_key), json=body, timeout=90)
    dt = round((time.time() - t0) * 1000, 2)
    try:
        payload = r.json()
    except Exception:
        payload = {"raw": r.text[:300]}
    return {
        "name": "non_stream",
        "mode": mode,
        "status": r.status_code,
        "latency_ms": dt,
        "preview": _preview_payload(payload, 320),
    }


def check_stream(api_key: str, mode: str) -> Dict[str, Any]:
    model = "claude-sonnet-4-5" if mode == "payg" else "kimi-for-coding"
    body = {
        "model": model,
        "max_tokens": 128,
        "stream": True,
        "messages": [{"role": "user", "content": f"请用三点中文回答：{mode} 流式检查"}],
    }
    data_lines = 0
    has_stop = False
    t0 = time.time()
    with httpx.Client(timeout=httpx.Timeout(connect=10, read=60, write=20, pool=20)) as c:
        with c.stream("POST", f"{VERIFY_KIMI_BASE}/v1/messages", headers=_headers(api_key), json=body) as r:
            status = r.status_code
            for line in r.iter_lines():
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                if line.startswith("data:"):
                    data_lines += 1
                    if "message_stop" in line or "[DONE]" in line:
                        has_stop = True
                        break
    dt = round((time.time() - t0) * 1000, 2)
    return {
        "name": "stream",
        "mode": mode,
        "status": status,
        "latency_ms": dt,
        "data_lines": data_lines,
        "has_stop": has_stop,
    }


def check_image(api_key: str, mode: str) -> Dict[str, Any]:
    if not VERIFY_KIMI_IMAGE_DATA_URL:
        return {
            "name": "image_sample",
            "mode": mode,
            "status": "skipped",
            "note": "VERIFY_KIMI_IMAGE_DATA_URL is not set; image check skipped.",
        }

    model = "claude-sonnet-4-5" if mode == "payg" else "kimi-for-coding"
    body = {
        "model": model,
        "max_tokens": 80,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": VERIFY_KIMI_IMAGE_DATA_URL}},
                    {"type": "text", "text": "请用中文简要描述图片中的主体。"},
                ],
            }
        ],
    }
    t0 = time.time()
    r = httpx.post(f"{VERIFY_KIMI_BASE}/v1/messages", headers=_headers(api_key), json=body, timeout=120)
    dt = round((time.time() - t0) * 1000, 2)
    try:
        payload = r.json()
    except Exception:
        payload = {"raw": r.text[:300]}
    return {
        "name": "image_sample",
        "mode": mode,
        "status": r.status_code,
        "latency_ms": dt,
        "preview": _preview_payload(payload, 320),
    }


def run_mode(mode: str) -> List[Dict[str, Any]]:
    api_key = _pick_key_for_mode(mode)
    effective_mode = _mode_for_key(api_key)
    return [
        check_models(api_key),
        check_non_stream(api_key, effective_mode),
        check_stream(api_key, effective_mode),
        check_image(api_key, effective_mode),
    ]


def main() -> int:
    print(f"[verify] base={VERIFY_KIMI_BASE}")
    print(
        "[verify] route_override="
        f"{VERIFY_KIMI_ROUTE_OVERRIDE or '<auto>'} image_data_url={'set' if VERIFY_KIMI_IMAGE_DATA_URL else 'unset'}"
    )

    try:
        results: List[Dict[str, Any]] = [check_health()]
        if VERIFY_KIMI_ROUTE_OVERRIDE in {"codingplan", "payg"}:
            results.extend(run_mode(VERIFY_KIMI_ROUTE_OVERRIDE))
        else:
            for mode in ("codingplan", "payg"):
                results.extend(run_mode(mode))
    except RuntimeError as exc:
        print(f"[verify] setup error: {exc}")
        print("[verify] hint: export VERIFY_KIMI_KEY before running auth checks.")
        return 2
    except Exception as exc:
        print(f"[verify] unexpected error: {type(exc).__name__}: {exc}")
        return 2

    print(json.dumps(results, ensure_ascii=False, indent=2))

    auth_fail = any(
        isinstance(item, dict) and item.get("status") == 401
        for item in results
        if item.get("name") in {"models", "non_stream", "stream", "image_sample"}
    )
    if auth_fail:
        print(
            "\n[hint] Gateway/upstream returned 401."
            " Check VERIFY_KIMI_KEY prefix (sk-kimi-* for codingplan), gateway .env base URLs,"
            " and KIMI_API_KEY/UPSTREAM_API_KEY availability."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
