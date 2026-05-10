import json
import os
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.log_mw import RequestLogMiddleware

load_dotenv()

def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic").rstrip("/")
MODEL_PRIMARY = os.getenv("MODEL_PRIMARY", "deepseek-v4-pro")
MODEL_FAST = os.getenv("MODEL_FAST", "deepseek-v4-flash")
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "https://pivot.claude.ai")
DEFAULT_MAX_TOKENS = _int_env("DEFAULT_MAX_TOKENS", 4096)
GATEWAY_PASSTHROUGH_METADATA = os.getenv("GATEWAY_PASSTHROUGH_METADATA", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
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
        thinking_value = item.get("thinking")
        signature = item.get("signature")
        if not isinstance(thinking_value, str) or not thinking_value.strip():
            dropped["invalid_thinking_block"] = dropped.get("invalid_thinking_block", 0) + 1
            return None
        out: Dict[str, Any] = {"type": "thinking", "thinking": thinking_value}
        if isinstance(signature, str) and signature:
            out["signature"] = signature
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


def _metadata_summary(metadata_value: Any) -> str:
    meta_type = type(metadata_value).__name__
    if isinstance(metadata_value, dict):
        keys = sorted(str(k) for k in metadata_value.keys())
        keys_preview = keys[:20]
        return (
            f"type=dict keys={keys_preview} keys_count={len(keys)} "
            f"size_hint=top_level_items:{len(metadata_value)}"
        )
    if isinstance(metadata_value, list):
        return f"type=list size_hint=top_level_items:{len(metadata_value)}"
    if isinstance(metadata_value, str):
        return f"type=str size_hint=chars:{len(metadata_value)}"
    if isinstance(metadata_value, (bytes, bytearray)):
        return f"type={meta_type} size_hint=bytes:{len(metadata_value)}"
    return f"type={meta_type} size_hint=n/a"


def _sanitize_request_body(raw_body: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, int], List[str]]:
    dropped: Dict[str, int] = {}
    removed_fields: List[str] = []
    sanitized: Dict[str, Any] = {}
    metadata_present = "metadata" in raw_body
    if metadata_present:
        summary = _metadata_summary(raw_body.get("metadata"))
        mode = "passthrough_enabled" if GATEWAY_PASSTHROUGH_METADATA else "removed"
        print(f"[gateway metadata] present=yes mode={mode} {summary}")
    else:
        mode = "passthrough_enabled" if GATEWAY_PASSTHROUGH_METADATA else "removed"
        print(f"[gateway metadata] present=no mode={mode}")

    for key, value in raw_body.items():
        if key in TOP_LEVEL_ALLOWLIST or (key == "metadata" and GATEWAY_PASSTHROUGH_METADATA):
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
        if "disable_parallel_tool_use" in tool_choice:
            print(
                "[gateway compat] forwarding tool_choice.disable_parallel_tool_use as-is; "
                "upstream may ignore this field"
            )
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
        sanitized["max_tokens"] = DEFAULT_MAX_TOKENS
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


def _build_request_shape_summary(raw_body: Dict[str, Any], routed_model: str) -> Dict[str, Any]:
    model_value = raw_body.get("model")
    model = model_value if isinstance(model_value, str) and model_value else routed_model
    stream = bool(raw_body.get("stream", False))
    max_tokens = raw_body.get("max_tokens")
    top_keys = sorted(raw_body.keys())
    messages = raw_body.get("messages") if isinstance(raw_body.get("messages"), list) else []
    tools = raw_body.get("tools") if isinstance(raw_body.get("tools"), list) else []
    system_present = "system" in raw_body
    metadata_present = "metadata" in raw_body

    role_seq: List[str] = []
    block_type_counts: Dict[str, int] = {}

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        role_seq.append(str(role))

        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type")
                key = block_type if isinstance(block_type, str) and block_type else "__unknown__"
            else:
                key = "__non_dict__"
            block_type_counts[key] = block_type_counts.get(key, 0) + 1

    last_role = None
    last_shape = "none"
    last_size = 0
    if messages and isinstance(messages[-1], dict):
        last_message = messages[-1]
        last_role = last_message.get("role")
        last_content = last_message.get("content")
        if isinstance(last_content, str):
            last_shape = "str"
            last_size = len(last_content)
        elif isinstance(last_content, list):
            last_shape = "blocks"
            last_size = len(last_content)
        else:
            last_shape = type(last_content).__name__
            last_size = 0

    return {
        "model": model,
        "stream": stream,
        "max_tokens": max_tokens,
        "top_keys": top_keys,
        "messages_count": len(messages),
        "tools_count": len(tools),
        "system_present": system_present,
        "metadata_present": metadata_present,
        "role_seq": role_seq,
        "last_message": {
            "role": last_role,
            "shape": last_shape,
            "size": last_size,
        },
        "block_type_counts": dict(sorted(block_type_counts.items())),
    }


def _request_fingerprint_from_summary(summary: Dict[str, Any]) -> str:
    fp_source = {
        "model": summary.get("model"),
        "stream": summary.get("stream"),
        "max_tokens": summary.get("max_tokens"),
        "role_seq": summary.get("role_seq"),
        "messages_count": summary.get("messages_count"),
        "tools_count": summary.get("tools_count"),
        "system_present": summary.get("system_present"),
        "metadata_present": summary.get("metadata_present"),
        "last_message": summary.get("last_message"),
        "block_type_counts": summary.get("block_type_counts"),
    }
    payload = json.dumps(fp_source, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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
    req_summary = _build_request_shape_summary(raw_body, body["model"])
    req_fp = _request_fingerprint_from_summary(req_summary)
    print(
        "[gateway request-shape] "
        f"fp={req_fp} model={req_summary['model']} stream={req_summary['stream']} max_tokens={req_summary['max_tokens']} "
        f"top_keys={req_summary['top_keys']} messages={req_summary['messages_count']} tools={req_summary['tools_count']} "
        f"system_present={req_summary['system_present']} metadata_present={req_summary['metadata_present']} "
        f"role_seq={req_summary['role_seq']} last_message={req_summary['last_message']} "
        f"block_type_counts={req_summary['block_type_counts']}"
    )

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








