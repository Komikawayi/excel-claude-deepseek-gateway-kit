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

# Ensure project-local .env values win over stale User/Process environment vars.
load_dotenv(override=True)

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
MIN_COMPAT_MAX_TOKENS = _int_env("MIN_COMPAT_MAX_TOKENS", 16)
GATEWAY_PASSTHROUGH_METADATA = os.getenv("GATEWAY_PASSTHROUGH_METADATA", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# Claude-style aliases shown to Office plugin; routed internally to upstream models.
# Keep both short and snapshot IDs to maximize model-discovery compatibility.
ALIAS_SONNET = os.getenv("ALIAS_SONNET", "claude-sonnet-4-5").strip()
ALIAS_SONNET_VERSIONED = os.getenv("ALIAS_SONNET_VERSIONED", "claude-sonnet-4-5-20250929").strip()
ALIAS_OPUS = os.getenv("ALIAS_OPUS", "claude-opus-4-5").strip()
ALIAS_OPUS_VERSIONED = os.getenv("ALIAS_OPUS_VERSIONED", "claude-opus-4-5-20251101").strip()
ALIAS_HAIKU = os.getenv("ALIAS_HAIKU", "claude-haiku-4-5").strip()
ALIAS_HAIKU_VERSIONED = os.getenv("ALIAS_HAIKU_VERSIONED", "claude-haiku-4-5-20251001").strip()

# Discovery metadata (plugin-facing only; execution still routes to MODEL_PRIMARY / MODEL_FAST).
DISCOVERY_MAX_INPUT_TOKENS = _int_env("DISCOVERY_MAX_INPUT_TOKENS", 1000000)
DISCOVERY_MAX_TOKENS = _int_env("DISCOVERY_MAX_TOKENS", 64000)

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
        ALIAS_SONNET_VERSIONED: MODEL_PRIMARY,
        ALIAS_OPUS: MODEL_PRIMARY,
        ALIAS_OPUS_VERSIONED: MODEL_PRIMARY,
        ALIAS_HAIKU: MODEL_FAST,
        ALIAS_HAIKU_VERSIONED: MODEL_FAST,
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


def _looks_like_connection_probe(raw_body: Dict[str, Any]) -> bool:
    # Heuristic for Claude Office connection test probe; keep strict to avoid
    # affecting normal user requests with intentionally small max_tokens.
    if bool(raw_body.get("stream", False)):
        return False

    raw_max_tokens = raw_body.get("max_tokens")
    if not isinstance(raw_max_tokens, int) or raw_max_tokens <= 0 or raw_max_tokens > 1:
        return False

    for key in ("system", "tools", "metadata", "thinking", "output_config", "tool_choice"):
        if key in raw_body:
            return False

    messages = raw_body.get("messages")
    if not isinstance(messages, list) or len(messages) != 1:
        return False

    msg = messages[0]
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return False

    content = msg.get("content")
    if isinstance(content, str):
        size = len(content.strip())
        return 0 < size <= 4

    if isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict):
        block = content[0]
        if block.get("type") != "text":
            return False
        text = block.get("text")
        if isinstance(text, str):
            size = len(text.strip())
            return 0 < size <= 4

    return False


def _sanitize_request_body(raw_body: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, int], List[str]]:
    dropped: Dict[str, int] = {}
    removed_fields: List[str] = []
    sanitized: Dict[str, Any] = {}
    raw_max_tokens = raw_body.get("max_tokens")
    is_connection_probe = _looks_like_connection_probe(raw_body)
    probe_kind = "connection_test" if is_connection_probe else "normal"
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
    elif is_connection_probe and max_tokens < MIN_COMPAT_MAX_TOKENS:
        sanitized["max_tokens"] = MIN_COMPAT_MAX_TOKENS
        dropped["max_tokens_raised_for_compat"] = dropped.get("max_tokens_raised_for_compat", 0) + 1

    print(
        "[gateway compat] "
        f"probe_kind={probe_kind} raw_max_tokens={raw_max_tokens} "
        f"effective_max_tokens={sanitized.get('max_tokens')}"
    )

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


def _infer_synthetic_block_from_event(
    event_type: str, event_data: Dict[str, Any]
) -> Dict[str, Any]:
    # Tool-related deltas should default to a tool_use scaffold.
    if event_type == "content_block_delta":
        delta = event_data.get("delta")
        if isinstance(delta, dict):
            delta_type = delta.get("type")
            if delta_type == "input_json_delta":
                return {"type": "tool_use", "id": "", "name": "", "input": {}}
        return {"type": "text", "text": ""}
    if event_type == "content_block_stop":
        return {"type": "text", "text": ""}
    return {"type": "text", "text": ""}


def _normalize_input_json_for_stream(raw: str) -> Tuple[str, str]:
    text = raw if isinstance(raw, str) else ""
    if not text.strip():
        return "{}", "empty"

    try:
        parsed = json.loads(text)
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":")), "parsed_full"
    except Exception:
        pass

    decoder = json.JSONDecoder()
    pos = 0
    fragments: List[Any] = []

    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        try:
            obj, end = decoder.raw_decode(text, pos)
        except Exception:
            fragments = []
            break
        fragments.append(obj)
        pos = end

    if fragments:
        merged: Any = fragments[0]
        for obj in fragments[1:]:
            if isinstance(merged, dict) and isinstance(obj, dict):
                merged.update(obj)
            elif isinstance(merged, list) and isinstance(obj, list):
                merged.extend(obj)
            else:
                merged = obj
        return json.dumps(merged, ensure_ascii=False, separators=(",", ":")), f"merged_fragments:{len(fragments)}"

    return json.dumps({"raw": text}, ensure_ascii=False, separators=(",", ":")), "fallback_wrapped_raw"


@app.get("/healthz")
async def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models() -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    default_capabilities = {
        "batch": {"supported": False},
        "citations": {"supported": False},
        "code_execution": {"supported": False},
        "context_management": {"supported": False},
        "effort": {"supported": False},
        "image_input": {"supported": True},
        "pdf_input": {"supported": False},
        "structured_outputs": {"supported": True},
        "thinking": {"supported": False},
    }
    def _model_info(model_id: str, display_name: str) -> Dict[str, Any]:
        return {
            "type": "model",
            "id": model_id,
            "display_name": display_name,
            "created_at": now,
            "max_input_tokens": DISCOVERY_MAX_INPUT_TOKENS,
            "max_tokens": DISCOVERY_MAX_TOKENS,
            "capabilities": default_capabilities,
        }
    model_data = [
        _model_info(ALIAS_SONNET, "Claude Sonnet 4.5"),
        _model_info(ALIAS_SONNET_VERSIONED, "Claude Sonnet 4.5"),
        _model_info(ALIAS_OPUS, "Claude Opus 4.5"),
        _model_info(ALIAS_OPUS_VERSIONED, "Claude Opus 4.5"),
        _model_info(ALIAS_HAIKU, "Claude Haiku 4.5"),
        _model_info(ALIAS_HAIKU_VERSIONED, "Claude Haiku 4.5"),
    ]

    return {
        "data": model_data,
        "first_id": model_data[0]["id"],
        "last_id": model_data[-1]["id"],
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
                seen_block_starts: set[int] = set()
                block_kind_by_index: Dict[int, str] = {}
                tool_partial_json_by_index: Dict[int, str] = {}
                pending_event_name: str | None = None
                try:
                    def _emit_frame(event_name: str | None, data_payload: str | None) -> bytes:
                        out_lines: List[str] = []
                        if event_name:
                            out_lines.append(f"event: {event_name}")
                        if data_payload is not None:
                            for segment in data_payload.split("\n"):
                                out_lines.append(f"data: {segment}")
                        return ("\n".join(out_lines) + "\n\n").encode("utf-8")

                    async def _process_frame(frame_lines: List[str]):
                        nonlocal chunk_count, pending_event_name
                        if not frame_lines:
                            return

                        event_name: str | None = None
                        data_lines: List[str] = []
                        for frame_line in frame_lines:
                            if frame_line.startswith("event:"):
                                if event_name is None:
                                    event_name = frame_line[len("event:") :].strip()
                            elif frame_line.startswith("data:"):
                                data_value = frame_line[len("data:") :]
                                if data_value.startswith(" "):
                                    data_value = data_value[1:]
                                data_lines.append(data_value)

                        # Some upstreams split "event:" and "data:" across separate frames.
                        # Hold event name until data arrives so emitted frames stay well-formed.
                        if event_name and not data_lines:
                            pending_event_name = event_name
                            return
                        if not event_name and pending_event_name and data_lines:
                            event_name = pending_event_name
                            pending_event_name = None
                        if event_name and pending_event_name and data_lines:
                            pending_event_name = None

                        if not data_lines:
                            chunk_count += 1
                            yield ("\n".join(frame_lines) + "\n\n").encode("utf-8")
                            return

                        payload = "\n".join(data_lines)
                        if payload == "[DONE]":
                            chunk_count += 1
                            yield _emit_frame(event_name, payload)
                            return

                        try:
                            parsed = json.loads(payload)
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
                            chunk_count += 1
                            yield _emit_frame("error", safe_error)
                            return

                        event_type = parsed.get("type") if isinstance(parsed, dict) else None
                        event_index = parsed.get("index") if isinstance(parsed, dict) else None

                        if (
                            event_type in {"content_block_delta", "content_block_stop"}
                            and isinstance(event_index, int)
                            and event_index not in seen_block_starts
                        ):
                            synthetic_block = _infer_synthetic_block_from_event(event_type, parsed)
                            synthetic_payload = {
                                "type": "content_block_start",
                                "index": event_index,
                                "content_block": synthetic_block,
                            }
                            print(
                                "[gateway sse shim] synthetic_start=1 "
                                f"trigger_event={event_type} index={event_index} "
                                f"synthetic_type={synthetic_block.get('type')}"
                            )
                            chunk_count += 1
                            yield _emit_frame("content_block_start", json.dumps(synthetic_payload, ensure_ascii=False))
                            seen_block_starts.add(event_index)
                            block_kind_by_index[event_index] = str(synthetic_block.get("type", "text"))
                        elif event_type == "content_block_start" and isinstance(event_index, int):
                            seen_block_starts.add(event_index)
                            if isinstance(parsed.get("content_block"), dict):
                                cb_type = parsed["content_block"].get("type")
                                if isinstance(cb_type, str):
                                    block_kind_by_index[event_index] = cb_type

                        # Capture tool_use input_json_delta chunks and defer forwarding until stop.
                        if (
                            event_type == "content_block_delta"
                            and isinstance(event_index, int)
                            and block_kind_by_index.get(event_index) == "tool_use"
                        ):
                            delta = parsed.get("delta")
                            if isinstance(delta, dict) and delta.get("type") == "input_json_delta":
                                partial_json = delta.get("partial_json")
                                if isinstance(partial_json, str):
                                    tool_partial_json_by_index[event_index] = (
                                        tool_partial_json_by_index.get(event_index, "") + partial_json
                                    )
                                    print(
                                        "[gateway sse shim] buffered_tool_input_delta=1 "
                                        f"index={event_index} chunk_len={len(partial_json)} "
                                        f"total_len={len(tool_partial_json_by_index[event_index])}"
                                    )
                                    # Skip fragmented tool input deltas; emit normalized one on stop.
                                    return

                        if (
                            event_type == "content_block_stop"
                            and isinstance(event_index, int)
                            and block_kind_by_index.get(event_index) == "tool_use"
                            and event_index in tool_partial_json_by_index
                        ):
                            normalized_json, normalize_reason = _normalize_input_json_for_stream(
                                tool_partial_json_by_index[event_index]
                            )
                            shim_delta_payload = {
                                "type": "content_block_delta",
                                "index": event_index,
                                "delta": {
                                    "type": "input_json_delta",
                                    "partial_json": normalized_json,
                                },
                            }
                            print(
                                "[gateway sse shim] emitted_normalized_tool_input_delta=1 "
                                f"index={event_index} reason={normalize_reason} normalized_len={len(normalized_json)}"
                            )
                            chunk_count += 1
                            yield _emit_frame("content_block_delta", json.dumps(shim_delta_payload, ensure_ascii=False))
                            tool_partial_json_by_index.pop(event_index, None)

                        chunk_count += 1
                        yield _emit_frame(event_name, payload)

                    frame_buffer: List[str] = []
                    async for raw_line in upstream_stream.aiter_lines():
                        if raw_line is None:
                            continue
                        if isinstance(raw_line, bytes):
                            raw_line = raw_line.decode("utf-8", errors="replace")

                        if raw_line == "":
                            async for out_chunk in _process_frame(frame_buffer):
                                yield out_chunk
                            frame_buffer = []
                            continue

                        frame_buffer.append(raw_line)

                    if frame_buffer:
                        async for out_chunk in _process_frame(frame_buffer):
                            yield out_chunk
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
        err_type = type(exc).__name__
        err_msg = str(exc)
        print(f"[gateway upstream http error] type={err_type} message={err_msg}")
        raise HTTPException(
            status_code=502,
            detail={
                "type": "upstream_http_error",
                "error_type": err_type,
                "message": err_msg,
            },
        ) from exc

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









