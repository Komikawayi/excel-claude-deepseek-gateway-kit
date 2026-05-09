import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.log_mw import RequestLogMiddleware

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic").rstrip("/")
MODEL_PRIMARY = os.getenv("MODEL_PRIMARY", "deepseek-v4-pro")
MODEL_FAST = os.getenv("MODEL_FAST", "deepseek-v4-flash")
ALLOWED_ORIGINS_ENV = os.getenv("ALLOWED_ORIGINS", "").strip()
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "https://pivot.claude.ai").strip()

# Aliases shown to Excel/Claude UI; routed internally to DeepSeek models.
ALIAS_SONNET = os.getenv("ALIAS_SONNET", "claude-sonnet-4-6")
ALIAS_OPUS = os.getenv("ALIAS_OPUS", "claude-opus-4-1")
ALIAS_HAIKU = os.getenv("ALIAS_HAIKU", "claude-3-5-haiku-latest")

# DeepSeek Anthropic compatibility allowlist.
TOP_LEVEL_ALLOWLIST = {
    "model",
    "max_tokens",
    "messages",
    "stop_sequences",
    "stream",
    "system",
    "temperature",
    "thinking",
    "output_config",
    "top_p",
    "tools",
    "tool_choice",
}

SUPPORTED_CONTENT_BLOCK_TYPES = {"text", "thinking", "tool_use", "tool_result"}
UNSUPPORTED_CONTENT_BLOCK_TYPES = {
    "image",
    "document",
    "search_result",
    "redacted_thinking",
    "server_tool_use",
    "web_search_tool_result",
    "code_execution_tool_result",
    "mcp_tool_use",
    "mcp_tool_result",
    "container_upload",
}

app = FastAPI(title="Excel Claude -> DeepSeek Gateway", version="1.5.1")


def _build_cors_config() -> Tuple[List[str], str | None]:
    # Claude for Office on macOS may preflight from `null` or other WebView origins.
    if ALLOWED_ORIGINS_ENV:
        if ALLOWED_ORIGINS_ENV == "*":
            return [], ".*"
        origins = [item.strip() for item in ALLOWED_ORIGINS_ENV.split(",") if item.strip()]
        if origins:
            return origins, None

    default_origins = [ALLOWED_ORIGIN, "null"]
    seen = set()
    unique_origins: List[str] = []
    for origin in default_origins:
        if origin and origin not in seen:
            seen.add(origin)
            unique_origins.append(origin)
    return unique_origins, None


cors_allow_origins, cors_allow_origin_regex = _build_cors_config()

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins,
    allow_origin_regex=cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLogMiddleware)



def _extract_incoming_token(req: Request) -> str:
    auth = req.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return req.headers.get("x-api-key", "").strip()


def _resolve_upstream_key(req: Request) -> str:
    # Priority: env key > incoming token from Excel gateway form.
    if DEEPSEEK_API_KEY:
        return DEEPSEEK_API_KEY
    incoming = _extract_incoming_token(req)
    if incoming:
        return incoming
    raise HTTPException(status_code=401, detail="No API key available (env or incoming token)")


def _route_model(model_id: str) -> str:
    value = (model_id or "").strip()
    if not value:
        return MODEL_PRIMARY

    alias_map = {
        ALIAS_SONNET: MODEL_PRIMARY,
        ALIAS_OPUS: MODEL_PRIMARY,
        ALIAS_HAIKU: MODEL_FAST,
        "sonnet": MODEL_PRIMARY,
        "opus": MODEL_PRIMARY,
        "haiku": MODEL_FAST,
        "deepseek-v4-pro": MODEL_PRIMARY,
        "deepseek-v4-flash": MODEL_FAST,
    }
    return alias_map.get(value, MODEL_PRIMARY)


def _normalize_system(system_value: Any) -> Any:
    if isinstance(system_value, str):
        return system_value

    if isinstance(system_value, list):
        blocks: List[Dict[str, str]] = []
        for item in system_value:
            if isinstance(item, str):
                if item.strip():
                    blocks.append({"type": "text", "text": item})
                continue

            if not isinstance(item, dict):
                continue
            if item.get("type") != "text":
                continue

            text = item.get("text")
            if isinstance(text, str) and text.strip():
                blocks.append({"type": "text", "text": text})
        return blocks

    if isinstance(system_value, dict):
        if system_value.get("type") == "text" and isinstance(system_value.get("text"), str):
            return [{"type": "text", "text": system_value["text"]}]

    return str(system_value)


def _sanitize_content_block(item: Any, dropped: Dict[str, int]) -> Dict[str, Any] | None:
    if isinstance(item, str):
        if item.strip():
            return {"type": "text", "text": item}
        dropped["empty_string_block"] = dropped.get("empty_string_block", 0) + 1
        return None

    if not isinstance(item, dict):
        dropped["non_dict_block"] = dropped.get("non_dict_block", 0) + 1
        return None

    block_type = item.get("type")
    if not isinstance(block_type, str):
        dropped["missing_block_type"] = dropped.get("missing_block_type", 0) + 1
        return None

    if block_type in UNSUPPORTED_CONTENT_BLOCK_TYPES:
        key = f"unsupported_block:{block_type}"
        dropped[key] = dropped.get(key, 0) + 1
        return None

    if block_type not in SUPPORTED_CONTENT_BLOCK_TYPES:
        key = f"unknown_block:{block_type}"
        dropped[key] = dropped.get(key, 0) + 1
        return None

    if block_type == "text":
        text = item.get("text")
        if not isinstance(text, str):
            dropped["invalid_text_block"] = dropped.get("invalid_text_block", 0) + 1
            return None
        if not text.strip():
            dropped["empty_text_block"] = dropped.get("empty_text_block", 0) + 1
            return None
        return {"type": "text", "text": text}

    if block_type == "thinking":
        out: Dict[str, Any] = {"type": "thinking"}
        thinking_value = item.get("thinking")
        signature = item.get("signature")
        if isinstance(thinking_value, str) and thinking_value:
            out["thinking"] = thinking_value
        if isinstance(signature, str) and signature:
            out["signature"] = signature
        if "thinking" not in out and "signature" not in out:
            dropped["invalid_thinking_block"] = dropped.get("invalid_thinking_block", 0) + 1
            return None
        return out

    if block_type == "tool_use":
        tool_use_id = item.get("id")
        name = item.get("name")
        tool_input = item.get("input", {})
        if not isinstance(tool_use_id, str) or not tool_use_id:
            dropped["invalid_tool_use_id"] = dropped.get("invalid_tool_use_id", 0) + 1
            return None
        if not isinstance(name, str) or not name:
            dropped["invalid_tool_use_name"] = dropped.get("invalid_tool_use_name", 0) + 1
            return None
        if not isinstance(tool_input, dict):
            tool_input = {}
        return {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}

    if block_type == "tool_result":
        tool_use_id = item.get("tool_use_id")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            dropped["invalid_tool_result_id"] = dropped.get("invalid_tool_result_id", 0) + 1
            return None

        content = item.get("content")
        if isinstance(content, list):
            normalized_content: List[Dict[str, Any]] = []
            for sub_item in content:
                block = _sanitize_content_block(sub_item, dropped)
                if block:
                    normalized_content.append(block)
            if not normalized_content:
                dropped["empty_tool_result_content"] = dropped.get("empty_tool_result_content", 0) + 1
                return None
            content = normalized_content
        elif isinstance(content, str):
            if not content.strip():
                dropped["blank_tool_result_content"] = dropped.get("blank_tool_result_content", 0) + 1
                return None
        else:
            dropped["invalid_tool_result_content"] = dropped.get("invalid_tool_result_content", 0) + 1
            return None

        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}

    return None


def _normalize_messages(messages: Any, dropped: Dict[str, int]) -> List[Dict[str, Any]]:
    if not isinstance(messages, list):
        dropped["invalid_messages"] = dropped.get("invalid_messages", 0) + 1
        return []

    normalized: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            dropped["non_dict_message"] = dropped.get("non_dict_message", 0) + 1
            continue

        role = msg.get("role")
        if role not in {"user", "assistant"}:
            dropped[f"invalid_role:{role}"] = dropped.get(f"invalid_role:{role}", 0) + 1
            continue

        content = msg.get("content")
        if isinstance(content, str):
            if not content.strip():
                dropped["blank_string_message"] = dropped.get("blank_string_message", 0) + 1
                continue
            normalized.append({"role": role, "content": content})
            continue

        if isinstance(content, list):
            blocks: List[Dict[str, Any]] = []
            for item in content:
                block = _sanitize_content_block(item, dropped)
                if block:
                    blocks.append(block)
            if not blocks:
                dropped["empty_message_after_sanitize"] = dropped.get("empty_message_after_sanitize", 0) + 1
                continue
            normalized.append({"role": role, "content": blocks})
            continue

        dropped["invalid_message_content"] = dropped.get("invalid_message_content", 0) + 1

    return normalized


def _sanitize_tools(tools: Any, dropped: Dict[str, int]) -> List[Dict[str, Any]]:
    if not isinstance(tools, list):
        dropped["invalid_tools"] = dropped.get("invalid_tools", 0) + 1
        return []

    cleaned_tools: List[Dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            dropped["non_dict_tool"] = dropped.get("non_dict_tool", 0) + 1
            continue

        source = tool.get("custom") if isinstance(tool.get("custom"), dict) else tool
        name = source.get("name") if isinstance(source.get("name"), str) else tool.get("name")
        description = source.get("description") if isinstance(source.get("description"), str) else tool.get("description")
        input_schema = source.get("input_schema") if isinstance(source.get("input_schema"), dict) else tool.get("input_schema")

        if not isinstance(name, str) or not name:
            dropped["invalid_tool_name"] = dropped.get("invalid_tool_name", 0) + 1
            continue

        if not isinstance(input_schema, dict):
            # Keep API-compatible default schema rather than forwarding unknown shapes.
            input_schema = {"type": "object", "properties": {}}
            dropped["tool_schema_defaulted"] = dropped.get("tool_schema_defaulted", 0) + 1

        out_tool: Dict[str, Any] = {
            "name": name,
            "input_schema": input_schema,
        }
        if isinstance(description, str) and description:
            out_tool["description"] = description

        cleaned_tools.append(out_tool)

    return cleaned_tools


def _sanitize_thinking(thinking: Any, dropped: Dict[str, int]) -> Dict[str, Any] | None:
    if not isinstance(thinking, dict):
        dropped["invalid_thinking"] = dropped.get("invalid_thinking", 0) + 1
        return None

    out: Dict[str, Any] = {}
    if isinstance(thinking.get("type"), str):
        out["type"] = thinking["type"]
    if isinstance(thinking.get("budget_tokens"), int):
        out["budget_tokens"] = thinking["budget_tokens"]

    # Keep only compatible shape; if empty, drop field.
    return out or None


def _sanitize_output_config(output_config: Any, dropped: Dict[str, int]) -> Dict[str, Any] | None:
    if not isinstance(output_config, dict):
        dropped["invalid_output_config"] = dropped.get("invalid_output_config", 0) + 1
        return None

    effort = output_config.get("effort")
    if isinstance(effort, str) and effort:
        return {"effort": effort}

    dropped["output_config_dropped"] = dropped.get("output_config_dropped", 0) + 1
    return None


def _sanitize_request_body(raw_body: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, int], List[str]]:
    dropped: Dict[str, int] = {}
    removed_fields: List[str] = []
    sanitized: Dict[str, Any] = {}

    for key, value in raw_body.items():
        if key in TOP_LEVEL_ALLOWLIST:
            sanitized[key] = value
        else:
            removed_fields.append(key)

    sanitized["messages"] = _normalize_messages(sanitized.get("messages"), dropped)

    if "system" in sanitized:
        sanitized["system"] = _normalize_system(sanitized["system"])

    if "tools" in sanitized:
        tools = _sanitize_tools(sanitized["tools"], dropped)
        if tools:
            sanitized["tools"] = tools
        else:
            sanitized.pop("tools", None)

    if "tool_choice" in sanitized and isinstance(sanitized["tool_choice"], dict):
        tool_choice = dict(sanitized["tool_choice"])
        tool_choice.pop("disable_parallel_tool_use", None)
        sanitized["tool_choice"] = tool_choice

    if "thinking" in sanitized:
        normalized_thinking = _sanitize_thinking(sanitized["thinking"], dropped)
        if normalized_thinking:
            sanitized["thinking"] = normalized_thinking
        else:
            sanitized.pop("thinking", None)

    if "output_config" in sanitized:
        normalized_output = _sanitize_output_config(sanitized["output_config"], dropped)
        if normalized_output:
            sanitized["output_config"] = normalized_output
        else:
            sanitized.pop("output_config", None)

    max_tokens = sanitized.get("max_tokens")
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        sanitized["max_tokens"] = 1024
        dropped["max_tokens_defaulted"] = dropped.get("max_tokens_defaulted", 0) + 1

    return sanitized, dropped, removed_fields


def _extract_upstream_error_payload(response: httpx.Response) -> Dict[str, Any]:
    text = response.text
    try:
        return response.json()
    except ValueError:
        return {"error": {"type": "upstream_non_json_error", "message": text[:8000]}}


def _extract_error_payload_from_text(text: str) -> Dict[str, Any]:
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except ValueError:
        pass
    return {"error": {"type": "upstream_non_json_error", "message": text[:8000]}}


@app.get("/healthz")
async def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models() -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "data": [
            {
                "type": "model",
                "id": ALIAS_SONNET,
                "display_name": f"{ALIAS_SONNET} (routed to {MODEL_PRIMARY})",
                "created_at": now,
            },
            {
                "type": "model",
                "id": ALIAS_OPUS,
                "display_name": f"{ALIAS_OPUS} (routed to {MODEL_PRIMARY})",
                "created_at": now,
            },
            {
                "type": "model",
                "id": ALIAS_HAIKU,
                "display_name": f"{ALIAS_HAIKU} (routed to {MODEL_FAST})",
                "created_at": now,
            },
        ],
        "first_id": ALIAS_SONNET,
        "last_id": ALIAS_HAIKU,
        "has_more": False,
    }


@app.get("/models")
async def list_models_alias() -> Dict[str, Any]:
    # Some clients probe /models rather than /v1/models.
    return await list_models()


@app.post("/v1/messages")
async def create_message(req: Request):
    upstream_key = _resolve_upstream_key(req)

    try:
        raw_body = await req.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc

    if not isinstance(raw_body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")

    body, dropped, removed_fields = _sanitize_request_body(raw_body)
    body["model"] = _route_model(str(body.get("model", "")))

    if not body.get("messages"):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "type": "invalid_request_error",
                    "message": "No valid messages remain after gateway sanitization",
                },
                "dropped": dropped,
                "removed_fields": removed_fields,
            },
        )

    if dropped or removed_fields:
        print(f"[gateway sanitize] dropped={dropped} removed_fields={removed_fields}")

    upstream_url = f"{DEEPSEEK_BASE_URL}/v1/messages"

    headers = {
        "Authorization": f"Bearer {upstream_key}",
        "x-api-key": upstream_key,
        "anthropic-version": req.headers.get("anthropic-version", "2023-06-01"),
        "content-type": "application/json",
    }

    try:
        if bool(body.get("stream", False)):
            # Single upstream request in manual stream mode.
            # We inspect status once, then forward the same stream (no probe+replay).
            stream_client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=15.0, read=None, write=60.0, pool=60.0)
            )
            request = stream_client.build_request("POST", upstream_url, headers=headers, json=body)
            upstream_stream = await stream_client.send(request, stream=True)

            if upstream_stream.status_code >= 400:
                raw_error = await upstream_stream.aread()
                await upstream_stream.aclose()
                await stream_client.aclose()
                err = _extract_error_payload_from_text(raw_error.decode("utf-8", errors="ignore"))
                print(f"[gateway upstream error] status={upstream_stream.status_code} body={err}")
                return JSONResponse(status_code=upstream_stream.status_code, content=err)

            async def event_stream():
                chunk_count = 0
                try:
                    async for line in upstream_stream.aiter_lines():
                        if line is None:
                            continue
                        if isinstance(line, bytes):
                            line = line.decode("utf-8", errors="replace")

                        # Guard downstream JSON parsers: only forward valid `data:` JSON lines.
                        if line.startswith("data: "):
                            payload = line[6:]
                            if payload and payload != "[DONE]":
                                try:
                                    json.loads(payload)
                                except Exception as parse_exc:
                                    print(
                                        f"[gateway malformed sse data] {type(parse_exc).__name__}: {parse_exc}; payload_head={payload[:200]}"
                                    )
                                    safe_error = json.dumps(
                                        {
                                            "type": "error",
                                            "error": {
                                                "type": "gateway_bad_event",
                                                "message": "Dropped malformed upstream data event",
                                            },
                                        },
                                        ensure_ascii=False,
                                    )
                                    yield b"event: error\n"
                                    yield f"data: {safe_error}\n\n".encode("utf-8")
                                    continue

                        chunk_count += 1
                        yield (line + "\n").encode("utf-8")
                except Exception as exc:
                    print(f"[gateway stream error] {type(exc).__name__}: {exc}")
                    safe_error = json.dumps(
                        {
                            "type": "error",
                            "error": {
                                "type": "gateway_stream_error",
                                "message": f"Upstream stream interrupted: {type(exc).__name__}",
                            },
                        },
                        ensure_ascii=False,
                    )
                    yield b"event: error\n"
                    yield f"data: {safe_error}\n\n".encode("utf-8")
                finally:
                    await upstream_stream.aclose()
                    await stream_client.aclose()
                    print(f"[gateway stream closed] chunks={chunk_count}")

            return StreamingResponse(
                event_stream(),
                status_code=upstream_stream.status_code,
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )
        async with httpx.AsyncClient(timeout=60.0) as client:
            upstream = await client.post(upstream_url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc}") from exc

    if upstream.status_code >= 400:
        payload = _extract_upstream_error_payload(upstream)
        print(f"[gateway upstream error] status={upstream.status_code} body={payload}")
        return JSONResponse(status_code=upstream.status_code, content=payload)

    payload = _extract_upstream_error_payload(upstream)
    return JSONResponse(status_code=upstream.status_code, content=payload)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def fallback(path: str) -> JSONResponse:
    # Keep errors explicit for easier troubleshooting.
    return JSONResponse(status_code=404, content={"error": f"Unsupported path: /{path}"})






