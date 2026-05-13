import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict

def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default

LOG_BODY_LIMIT_BYTES = _int_env("LOG_BODY_LIMIT_BYTES", 65536)
LOG_BODY_PREVIEW_CHARS = _int_env("LOG_BODY_PREVIEW_CHARS", 2500)
LOG_CONTENT_REDACT = os.getenv("LOG_CONTENT_REDACT", "true").strip().lower() in {"1", "true", "yes", "on"}
LOG_CONTENT_MAX_CHARS = _int_env("LOG_CONTENT_MAX_CHARS", 200)
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

    if normalized in TOKEN_METRIC_ALLOWLIST:
        return False

    if normalized in SENSITIVE_EXACT_KEYS:
        return True

    if any(normalized.endswith(suffix) for suffix in SENSITIVE_KEY_SUFFIXES):
        return True

    if any(normalized.startswith(prefix) for prefix in SENSITIVE_KEY_PREFIXES):
        return True

    return False


def _truncate_content(value: Any) -> Any:
    """截断长文本内容（prompt、system、图片 base64 等）。"""
    if not LOG_CONTENT_REDACT:
        return value
    if isinstance(value, str) and len(value) > LOG_CONTENT_MAX_CHARS:
        return value[:LOG_CONTENT_MAX_CHARS] + f"...<truncated,{len(value)}chars>"
    return value


def _redact_content_blocks(blocks: Any) -> Any:
    """脱敏 messages 中的 content blocks（保留类型信息，截断内容）。"""
    if not isinstance(blocks, list):
        return _truncate_content(blocks)
    result = []
    for block in blocks:
        if not isinstance(block, dict):
            result.append(_truncate_content(block))
            continue
        block_type = block.get("type", "")
        redacted_block: Dict[str, Any] = {"type": block_type}
        if block_type == "text":
            text = block.get("text", "")
            redacted_block["text"] = _truncate_content(text)
            redacted_block["_len"] = len(text) if isinstance(text, str) else 0
        elif block_type == "image":
            redacted_block["source"] = {"type": block.get("source", {}).get("type", ""), "_data": "<image redacted>"}
        elif block_type == "tool_use":
            redacted_block["id"] = block.get("id", "")
            redacted_block["name"] = block.get("name", "")
            redacted_block["input"] = _truncate_content(json.dumps(block.get("input", {}), ensure_ascii=False))
        elif block_type == "tool_result":
            redacted_block["tool_use_id"] = block.get("tool_use_id", "")
            content = block.get("content", "")
            redacted_block["content"] = _truncate_content(
                content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            )
        else:
            redacted_block["_raw"] = _truncate_content(json.dumps(block, ensure_ascii=False))
        result.append(redacted_block)
    return result


def _redact_messages(messages: Any) -> Any:
    """脱敏 messages 列表（保留角色和结构，截断内容）。"""
    if not isinstance(messages, list):
        return messages
    result = []
    for msg in messages:
        if not isinstance(msg, dict):
            result.append(msg)
            continue
        role = msg.get("role", "?")
        content = msg.get("content")
        if isinstance(content, str):
            result.append({"role": role, "content": _truncate_content(content), "_len": len(content)})
        elif isinstance(content, list):
            result.append({"role": role, "content": _redact_content_blocks(content)})
        else:
            result.append({"role": role, "content": content})
    return result


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for k, v in value.items():
            if _is_sensitive_key(k):
                redacted[k] = "***REDACTED***"
            elif k == "messages":
                redacted[k] = _redact_messages(v)
            elif k == "system":
                redacted[k] = _truncate_content(v) if isinstance(v, str) else _redact_content_blocks(v)
            elif k == "thinking":
                redacted[k] = _truncate_content(v) if isinstance(v, str) else _redact_payload(v)
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
        api_key_flag = "yes" if headers.get("api-key") else "no"
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
            f"api-key={api_key_flag} "
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
