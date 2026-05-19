import json
import os
import time

import httpx

BASE = os.getenv("VERIFY_MIMO_BASE", "http://127.0.0.1:8789").rstrip("/")
VERIFY_MIMO_KEY = os.getenv("VERIFY_MIMO_KEY", "sk-test-placeholder")
HEADERS = {
    "x-api-key": VERIFY_MIMO_KEY,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}


def check_health() -> dict:
    response = httpx.get(f"{BASE}/healthz", timeout=10)
    return {"name": "healthz", "status": response.status_code, "body": response.text[:200]}


def check_models() -> dict:
    response = httpx.get(f"{BASE}/v1/models", headers={"x-api-key": VERIFY_MIMO_KEY}, timeout=15)
    ok = False
    try:
        payload = response.json()
        ok = isinstance(payload, dict) and isinstance(payload.get("data"), list) and len(payload["data"]) > 0
    except Exception:
        payload = response.text[:300]
    return {"name": "models", "status": response.status_code, "ok": ok, "preview": str(payload)[:300]}


def check_non_stream() -> dict:
    body = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 64,
        "stream": False,
        "messages": [{"role": "user", "content": "请用中文回复：MIMO 网关连接检查通过"}],
    }
    t0 = time.time()
    response = httpx.post(f"{BASE}/v1/messages", headers=HEADERS, json=body, timeout=90)
    latency_ms = round((time.time() - t0) * 1000, 2)
    try:
        payload = response.json()
    except Exception:
        payload = {"raw": response.text[:300]}
    return {
        "name": "non_stream",
        "status": response.status_code,
        "latency_ms": latency_ms,
        "preview": json.dumps(payload, ensure_ascii=False)[:300],
    }


def check_stream() -> dict:
    body = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 96,
        "stream": True,
        "messages": [{"role": "user", "content": "请用三点中文回答：MIMO 流式检查"}],
    }
    data_lines = 0
    has_stop = False
    status = 0
    t0 = time.time()
    with httpx.Client(timeout=httpx.Timeout(connect=10, read=60, write=20, pool=20)) as client:
        with client.stream("POST", f"{BASE}/v1/messages", headers=HEADERS, json=body) as response:
            status = response.status_code
            for line in response.iter_lines():
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                if line.startswith("data:"):
                    data_lines += 1
                    if "message_stop" in line or "[DONE]" in line:
                        has_stop = True
                        break
    latency_ms = round((time.time() - t0) * 1000, 2)
    return {
        "name": "stream",
        "status": status,
        "latency_ms": latency_ms,
        "data_lines": data_lines,
        "has_stop": has_stop,
    }


if __name__ == "__main__":
    results = [check_health(), check_models(), check_non_stream(), check_stream()]
    print(json.dumps(results, ensure_ascii=False, indent=2))

    auth_fail = any(
        isinstance(item, dict) and item.get("status") == 401
        for item in results
        if item.get("name") in {"non_stream", "stream"}
    )
    if auth_fail:
        print(
            "\n[hint] non_stream/stream returned 401. Check MIMO_API_KEY, incoming key prefix (must be sk- or tp-), and base URL configuration."
        )
