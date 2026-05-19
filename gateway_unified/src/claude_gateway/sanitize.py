import json
from typing import Any, Dict, List, Tuple

# 内容大小限制（防止超大 payload 耗尽内存）
MAX_TEXT_BLOCK_LEN = 200_000       # 单个 text block 最大字符数
MAX_IMAGE_BASE64_LEN = 10_000_000 # base64 图片数据最大字符数 (~7.5MB)
MAX_TOOL_DESC_LEN = 8_000         # tool description 最大字符数
MAX_TOOL_INPUT_DEPTH = 10         # tool_use.input 最大嵌套层数
MAX_MESSAGES = 1000               # messages 数组最大条数
WEB_SEARCH_TOOL_TYPE_PREFIX = "web_search_"
WEB_SEARCH_ALLOWED_TOOL_TYPES = {"web_search_20250305"}
WEB_SEARCH_CONTENT_BLOCK_TYPES = {"server_tool_use", "web_search_tool_result"}

# 默认允许透传的顶层字段
DEFAULT_TOP_LEVEL_ALLOWLIST = {
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

# 通用支持的 content block 类型（所有 provider 共同支持）
BASE_SUPPORTED_CONTENT_BLOCK_TYPES = {"text", "thinking", "tool_use", "tool_result"}

# 需要显式过滤的不支持类型（部分可通过开关放通）
UNSUPPORTED_CONTENT_BLOCK_TYPES = {
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


def get_supported_content_types(
    image_support: bool = False,
    allow_server_web_search_blocks: bool = False,
) -> set:
    """根据 provider 能力返回支持的 content block 类型集合。"""
    types = set(BASE_SUPPORTED_CONTENT_BLOCK_TYPES)
    if image_support:
        types |= {"image", "image_url"}
    if allow_server_web_search_blocks:
        types |= WEB_SEARCH_CONTENT_BLOCK_TYPES
    return types


def normalize_system(system_value: Any) -> Any:
    """规范化 system 字段，兼容字符串、列表、字典格式。"""
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


def _dict_nesting_depth(obj: Any, current: int = 0) -> int:
    """计算 dict/list 嵌套深度。"""
    if isinstance(obj, dict):
        if not obj:
            return current
        return max(_dict_nesting_depth(v, current + 1) for v in obj.values())
    if isinstance(obj, list):
        if not obj:
            return current
        return max(_dict_nesting_depth(v, current + 1) for v in obj)
    return current


def sanitize_content_block(
    item: Any,
    dropped: Dict[str, int],
    supported_types: set,
    allow_server_web_search_blocks: bool = False,
) -> Dict[str, Any] | None:
    """消毒单个 content block，返回规范化后的 block 或 None（表示丢弃）。"""
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

    if (
        block_type in UNSUPPORTED_CONTENT_BLOCK_TYPES
        and not (allow_server_web_search_blocks and block_type in WEB_SEARCH_CONTENT_BLOCK_TYPES)
    ):
        key = f"unsupported_block:{block_type}"
        dropped[key] = dropped.get(key, 0) + 1
        return None

    if block_type not in supported_types:
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
        if len(text) > MAX_TEXT_BLOCK_LEN:
            text = text[:MAX_TEXT_BLOCK_LEN]
            dropped["truncated_text_block"] = dropped.get("truncated_text_block", 0) + 1
        return {"type": "text", "text": text}

    if block_type == "thinking":
        thinking_value = item.get("thinking")
        signature = item.get("signature")
        if not isinstance(thinking_value, str) or not thinking_value.strip():
            # 尝试降级为 text block（Kimi 兼容行为）
            fallback_text = item.get("text")
            if isinstance(fallback_text, str) and fallback_text.strip():
                dropped["invalid_thinking_block_downgraded"] = dropped.get("invalid_thinking_block_downgraded", 0) + 1
                return {"type": "text", "text": fallback_text}
            dropped["invalid_thinking_block"] = dropped.get("invalid_thinking_block", 0) + 1
            return None
        out: Dict[str, Any] = {"type": "thinking", "thinking": thinking_value}
        if isinstance(signature, str) and signature:
            out["signature"] = signature
        return out

    if block_type == "image":
        source = item.get("source")
        if not isinstance(source, dict):
            dropped["invalid_image_source"] = dropped.get("invalid_image_source", 0) + 1
            return None

        source_type = source.get("type")
        if source_type == "base64":
            media_type = source.get("media_type")
            data = source.get("data")
            if not isinstance(media_type, str) or not media_type.strip():
                dropped["invalid_image_media_type"] = dropped.get("invalid_image_media_type", 0) + 1
                return None
            if not isinstance(data, str) or not data.strip():
                dropped["invalid_image_data"] = dropped.get("invalid_image_data", 0) + 1
                return None
            if len(data) > MAX_IMAGE_BASE64_LEN:
                dropped["oversized_image_base64"] = dropped.get("oversized_image_base64", 0) + 1
                return None
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": data},
            }

        if source_type == "url":
            url = source.get("url")
            if not isinstance(url, str) or not url.strip():
                dropped["invalid_image_url"] = dropped.get("invalid_image_url", 0) + 1
                return None
            return {"type": "image", "source": {"type": "url", "url": url}}

        dropped["unsupported_image_source_type"] = dropped.get("unsupported_image_source_type", 0) + 1
        return None

    if block_type == "image_url":
        image_url = item.get("image_url")
        if isinstance(image_url, dict):
            url = image_url.get("url")
            detail = image_url.get("detail")
            if not isinstance(url, str) or not url.strip():
                dropped["invalid_image_url_block"] = dropped.get("invalid_image_url_block", 0) + 1
                return None
            out_image_url: Dict[str, Any] = {"url": url}
            if isinstance(detail, str) and detail:
                out_image_url["detail"] = detail
            return {"type": "image_url", "image_url": out_image_url}
        if isinstance(image_url, str) and image_url.strip():
            return {"type": "image_url", "image_url": {"url": image_url}}
        dropped["invalid_image_url_block"] = dropped.get("invalid_image_url_block", 0) + 1
        return None

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
        if _dict_nesting_depth(tool_input) > MAX_TOOL_INPUT_DEPTH:
            dropped["oversized_tool_input_depth"] = dropped.get("oversized_tool_input_depth", 0) + 1
            return None
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
                block = sanitize_content_block(sub_item, dropped, supported_types)
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

    if block_type == "server_tool_use":
        # MVP: 仅放通 web_search server tool 的必要字段，避免污染其它 server tool。
        tool_use_id = item.get("id")
        name = item.get("name")
        tool_input = item.get("input", {})
        if not isinstance(tool_use_id, str) or not tool_use_id:
            dropped["invalid_server_tool_use_id"] = dropped.get("invalid_server_tool_use_id", 0) + 1
            return None
        if not isinstance(name, str) or not name:
            dropped["invalid_server_tool_use_name"] = dropped.get("invalid_server_tool_use_name", 0) + 1
            return None
        if not isinstance(tool_input, dict):
            dropped["invalid_server_tool_use_input"] = dropped.get("invalid_server_tool_use_input", 0) + 1
            return None
        if _dict_nesting_depth(tool_input) > MAX_TOOL_INPUT_DEPTH:
            dropped["oversized_server_tool_input_depth"] = dropped.get("oversized_server_tool_input_depth", 0) + 1
            return None
        return {
            "type": "server_tool_use",
            "id": tool_use_id,
            "name": name,
            "input": tool_input,
        }

    if block_type == "web_search_tool_result":
        tool_use_id = item.get("tool_use_id")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            dropped["invalid_web_search_tool_result_id"] = dropped.get("invalid_web_search_tool_result_id", 0) + 1
            return None

        raw_content = item.get("content")
        if isinstance(raw_content, list):
            content_list: List[Dict[str, Any]] = []
            for entry in raw_content:
                if isinstance(entry, dict):
                    content_list.append(entry)
            if not content_list:
                dropped["invalid_web_search_tool_result_content"] = dropped.get(
                    "invalid_web_search_tool_result_content",
                    0,
                ) + 1
                return None
            return {
                "type": "web_search_tool_result",
                "tool_use_id": tool_use_id,
                "content": content_list,
            }

        if isinstance(raw_content, dict):
            return {
                "type": "web_search_tool_result",
                "tool_use_id": tool_use_id,
                "content": raw_content,
            }

        dropped["invalid_web_search_tool_result_content"] = dropped.get(
            "invalid_web_search_tool_result_content",
            0,
        ) + 1
        return None

    return None


def normalize_messages(
    messages: Any,
    dropped: Dict[str, int],
    supported_types: set,
    allow_server_web_search_blocks: bool = False,
) -> List[Dict[str, Any]]:
    """规范化 messages 数组，过滤无效消息和 content block。"""
    if not isinstance(messages, list):
        dropped["invalid_messages"] = dropped.get("invalid_messages", 0) + 1
        return []

    if len(messages) > MAX_MESSAGES:
        dropped["truncated_messages"] = dropped.get("truncated_messages", 0) + 1
        messages = messages[-MAX_MESSAGES:]  # 保留最新的消息

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
                block = sanitize_content_block(
                    item,
                    dropped,
                    supported_types,
                    allow_server_web_search_blocks=allow_server_web_search_blocks,
                )
                if block:
                    blocks.append(block)
            if not blocks:
                dropped["empty_message_after_sanitize"] = dropped.get("empty_message_after_sanitize", 0) + 1
                continue
            normalized.append({"role": role, "content": blocks})
            continue

        dropped["invalid_message_content"] = dropped.get("invalid_message_content", 0) + 1

    return normalized


def sanitize_tools(
    tools: Any,
    dropped: Dict[str, int],
    enable_web_search_tool: bool = False,
    normalize_web_search_as_client_tool: bool = False,
) -> List[Dict[str, Any]]:
    """规范化 tools 数组。"""
    if not isinstance(tools, list):
        dropped["invalid_tools"] = dropped.get("invalid_tools", 0) + 1
        return []

    cleaned_tools: List[Dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            dropped["non_dict_tool"] = dropped.get("non_dict_tool", 0) + 1
            continue

        tool_type = tool.get("type")
        if enable_web_search_tool and isinstance(tool_type, str) and (
            tool_type in WEB_SEARCH_ALLOWED_TOOL_TYPES or tool_type.startswith(WEB_SEARCH_TOOL_TYPE_PREFIX)
        ):
            if normalize_web_search_as_client_tool:
                # 强制统一为 client tool 语义，避免 server tool/client tool 混用。
                cleaned_tools.append(
                    {
                        "name": "web_search",
                        "description": "Search the web and return relevant results with URLs.",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                            },
                            "required": ["query"],
                        },
                    }
                )
            else:
                # server tool 透传模式（不开启本地自动执行时使用）
                cleaned_tools.append(dict(tool))
            continue

        source = tool.get("custom") if isinstance(tool.get("custom"), dict) else tool
        name = source.get("name") if isinstance(source.get("name"), str) else tool.get("name")
        description = source.get("description") if isinstance(source.get("description"), str) else tool.get("description")
        input_schema = source.get("input_schema") if isinstance(source.get("input_schema"), dict) else tool.get("input_schema")

        if not isinstance(name, str) or not name:
            dropped["invalid_tool_name"] = dropped.get("invalid_tool_name", 0) + 1
            continue

        if not isinstance(input_schema, dict):
            input_schema = {"type": "object", "properties": {}}
            dropped["tool_schema_defaulted"] = dropped.get("tool_schema_defaulted", 0) + 1

        out_tool: Dict[str, Any] = {
            "name": name,
            "input_schema": input_schema,
        }
        if isinstance(description, str) and description:
            out_tool["description"] = description[:MAX_TOOL_DESC_LEN]

        cleaned_tools.append(out_tool)

    return cleaned_tools


def sanitize_thinking(thinking: Any, dropped: Dict[str, int]) -> Dict[str, Any] | None:
    """规范化 thinking 参数。"""
    if not isinstance(thinking, dict):
        dropped["invalid_thinking"] = dropped.get("invalid_thinking", 0) + 1
        return None

    out: Dict[str, Any] = {}
    if isinstance(thinking.get("type"), str):
        out["type"] = thinking["type"]
    if isinstance(thinking.get("budget_tokens"), int):
        out["budget_tokens"] = thinking["budget_tokens"]

    return out or None


def sanitize_output_config(output_config: Any, dropped: Dict[str, int]) -> Dict[str, Any] | None:
    """规范化 output_config 参数。"""
    if not isinstance(output_config, dict):
        dropped["invalid_output_config"] = dropped.get("invalid_output_config", 0) + 1
        return None

    effort = output_config.get("effort")
    if isinstance(effort, str) and effort:
        return {"effort": effort}

    dropped["output_config_dropped"] = dropped.get("output_config_dropped", 0) + 1
    return None


def metadata_summary(metadata_value: Any) -> str:
    """生成 metadata 字段的摘要字符串用于日志。"""
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


def looks_like_connection_probe(raw_body: Dict[str, Any]) -> bool:
    """检测是否为 Office 插件的连接探测请求。"""
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


def sanitize_request_body(
    raw_body: Dict[str, Any],
    image_support: bool = False,
    passthrough_metadata: bool = False,
    enable_web_search_tool: bool = False,
    normalize_web_search_as_client_tool: bool = False,
    default_max_tokens: int = 4096,
    min_compat_max_tokens: int = 16,
) -> Tuple[Dict[str, Any], Dict[str, int], List[str]]:
    """完整的请求体消毒流程。返回 (sanitized_body, dropped_stats, removed_fields)。"""
    dropped: Dict[str, int] = {}
    removed_fields: List[str] = []
    sanitized: Dict[str, Any] = {}
    raw_max_tokens = raw_body.get("max_tokens")
    is_probe = looks_like_connection_probe(raw_body)
    probe_kind = "connection_test" if is_probe else "normal"
    allow_server_web_search_blocks = enable_web_search_tool and (not normalize_web_search_as_client_tool)
    supported_types = get_supported_content_types(
        image_support=image_support,
        allow_server_web_search_blocks=allow_server_web_search_blocks,
    )

    metadata_present = "metadata" in raw_body
    if metadata_present:
        summary = metadata_summary(raw_body.get("metadata"))
        mode = "passthrough_enabled" if passthrough_metadata else "removed"
        print(f"[gateway metadata] present=yes mode={mode} {summary}")
    else:
        mode = "passthrough_enabled" if passthrough_metadata else "removed"
        print(f"[gateway metadata] present=no mode={mode}")

    for key, value in raw_body.items():
        if key in DEFAULT_TOP_LEVEL_ALLOWLIST or (key == "metadata" and passthrough_metadata):
            sanitized[key] = value
        else:
            removed_fields.append(key)

    sanitized["messages"] = normalize_messages(
        sanitized.get("messages"),
        dropped,
        supported_types,
        allow_server_web_search_blocks=allow_server_web_search_blocks,
    )

    if "system" in sanitized:
        sanitized["system"] = normalize_system(sanitized["system"])

    if "tools" in sanitized:
        tools = sanitize_tools(
            sanitized["tools"],
            dropped,
            enable_web_search_tool=enable_web_search_tool,
            normalize_web_search_as_client_tool=normalize_web_search_as_client_tool,
        )
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
        normalized_thinking = sanitize_thinking(sanitized["thinking"], dropped)
        if normalized_thinking:
            sanitized["thinking"] = normalized_thinking
        else:
            sanitized.pop("thinking", None)

    if "output_config" in sanitized:
        normalized_output = sanitize_output_config(sanitized["output_config"], dropped)
        if normalized_output:
            sanitized["output_config"] = normalized_output
        else:
            sanitized.pop("output_config", None)

    max_tokens = sanitized.get("max_tokens")
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        sanitized["max_tokens"] = default_max_tokens
        dropped["max_tokens_defaulted"] = dropped.get("max_tokens_defaulted", 0) + 1
    elif is_probe and max_tokens < min_compat_max_tokens:
        sanitized["max_tokens"] = min_compat_max_tokens
        dropped["max_tokens_raised_for_compat"] = dropped.get("max_tokens_raised_for_compat", 0) + 1

    print(
        "[gateway compat] "
        f"probe_kind={probe_kind} raw_max_tokens={raw_max_tokens} "
        f"effective_max_tokens={sanitized.get('max_tokens')}"
    )

    return sanitized, dropped, removed_fields
