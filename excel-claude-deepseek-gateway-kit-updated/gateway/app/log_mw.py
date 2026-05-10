import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict

LOG_BODY_LIMIT_BYTES = int(os.getenv("LOG_BODY_LIMIT_BYTES", "65536"))
LOG_BODY_PREVIEW_CHARS = int(os.getenv("LOG_BODY_PREVIEW_CHARS", "2500"))
SENSITIVE_EXACT_KEYS = {
    "authorization",
    "x-api-key",
    "api-key",
    "token",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "cookie",
    "set-cookie",
}

SENSITIVE_KEY_SUFFIXES = (
    "_api_key",
    "_apikey",
    "_access_token",
    "_refresh_token",
    "_auth_token",
    "_bearer_token",
    "_session_token",
    "_secret",
    "_password",
    "_cookie",
)

SENSITIVE_KEY_PREFIXES = (
    "api_key_",
    "apikey_",
    "access_token_",
    "refresh_token_",
    "auth_token_",
    "secret_",
    "password_",
    "cookie_",
)

TOKEN_METRIC_ALLOWLIST = {
    "max_tokens",
    "input_tokens",
    "output_tokens",
    "budget_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "total_tokens",
}


def _normalize_key(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_")


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalize_key(key)

    # Token counters are operational metrics, not secrets.
    if normalized in TOKEN_METRIC_ALLOWLIST:
        return False

    if normalized in SENSITIVE_EXACT_KEYS:
        return True

    if any(normalized.endswith(suffix) for suffix in SENSITIVE_KEY_SUFFIXES):
        return True

    if any(normalized.startswith(prefix) for prefix in SENSITIVE_KEY_PREFIXES):
        return True

    return False


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for k, v in value.items():
            if _is_sensitive_key(k):
                redacted[k] = "***REDACTED***"
            else:
                redacted[k] = _redact_payload(v)
        return redacted
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    return value


class RequestLogMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        started_at = time.perf_counter()
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        method = scope.get("method", "")
        path = scope.get("path", "")

        headers_list = scope.get("headers", [])
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in headers_list}

        body_chunks = []
        seen_more_body = False
        body_truncated = False
        captured_bytes = 0

        async def wrapped_receive():
            nonlocal seen_more_body, body_truncated, captured_bytes
            message = await receive()
            if message.get("type") == "http.request":
                chunk = message.get("body", b"")
                if chunk:
                    if captured_bytes < LOG_BODY_LIMIT_BYTES:
                        remaining = LOG_BODY_LIMIT_BYTES - captured_bytes
                        take = chunk[:remaining]
                        if take:
                            body_chunks.append(take)
                            captured_bytes += len(take)
                        if len(chunk) > remaining:
                            body_truncated = True
                    else:
                        body_truncated = True
                if message.get("more_body", False):
                    seen_more_body = True
            return message

        status_code = None
        content_type = None

        async def wrapped_send(message):
            nonlocal status_code, content_type
            if message.get("type") == "http.response.start":
                status_code = message.get("status")
                resp_headers = {
                    k.decode("latin-1").lower(): v.decode("latin-1")
                    for k, v in message.get("headers", [])
                }
                content_type = resp_headers.get("content-type")
            await send(message)

        await self.app(scope, wrapped_receive, wrapped_send)

        body_bytes = b"".join(body_chunks)
        body_str = body_bytes.decode("utf-8", errors="ignore")
        body_preview = body_str

        body_summary = ""
        parsed_json = False
        try:
            parsed = json.loads(body_str) if body_str else None
            if isinstance(parsed, dict):
                keys = sorted(parsed.keys())
                msg_count = len(parsed.get("messages", [])) if isinstance(parsed.get("messages"), list) else 0
                tool_count = len(parsed.get("tools", [])) if isinstance(parsed.get("tools"), list) else 0
                body_summary = f" keys={keys} messages={msg_count} tools={tool_count}"
                body_preview = json.dumps(_redact_payload(parsed), ensure_ascii=False)
                parsed_json = True
        except Exception:
            body_summary = ""

        if body_str and not parsed_json:
            body_preview = "[non-json body omitted]"

        body_preview = body_preview[:LOG_BODY_PREVIEW_CHARS]
        if body_truncated:
            body_preview += " ...<truncated>"

        auth_flag = "yes" if headers.get("authorization") else "no"
        key_flag = "yes" if headers.get("x-api-key") else "no"
        origin = headers.get("origin")
        req_content_type = headers.get("content-type")
        anthropic_version = headers.get("anthropic-version")
        anthropic_beta = headers.get("anthropic-beta")
        acr_method = headers.get("access-control-request-method")
        acr_headers = headers.get("access-control-request-headers")
        latency_ms = round((time.perf_counter() - started_at) * 1000, 2)

        print(f"[{ts}] {method} {path}")
        print(
            f"[{ts}] headers: origin={origin} auth={auth_flag} x-api-key={key_flag} "
            f"ct={req_content_type} anthropic-version={anthropic_version} anthropic-beta={anthropic_beta} "
            f"acr-method={acr_method} acr-headers={acr_headers}{body_summary}"
        )
        print(f"[{ts}] body: {body_preview}")
        print(
            f"[{ts}] response: {status_code} ct={content_type} latency_ms={latency_ms} "
            f"captured_bytes={captured_bytes} body_truncated={body_truncated}"
        )

        if seen_more_body:
            print(f"[{ts}] note: request body was chunked (more_body=True seen)")
