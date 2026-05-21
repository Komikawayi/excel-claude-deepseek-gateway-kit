import json
from typing import Any, Dict, List, Tuple


def infer_synthetic_block_from_event(event_type: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
    """根据 SSE 事件推断缺失的 content_block_start 应该使用的 block 类型。"""
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


def normalize_input_json_for_stream(raw: str) -> Tuple[str, str]:
    """将分片的 tool input JSON 合并为完整 JSON。"""
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


def emit_frame(event_name: str | None, data_payload: str | None) -> bytes:
    """构造一个 SSE 帧。"""
    out_lines: List[str] = []
    if event_name:
        out_lines.append(f"event: {event_name}")
    if data_payload is not None:
        for segment in data_payload.split("\n"):
            out_lines.append(f"data: {segment}")
    return ("\n".join(out_lines) + "\n\n").encode("utf-8")


async def process_sse_frame(
    frame_lines: List[str],
    seen_block_starts: set,
    block_kind_by_index: Dict[int, str],
    tool_partial_json_by_index: Dict[int, str],
    pending_event_name: str | None,
) -> Tuple[List[bytes], str | None]:
    """
    处理一个 SSE 帧，返回 (输出字节列表, 更新后的 pending_event_name)。

    处理逻辑：
    - 补全缺失的 content_block_start（synthetic shim）
    - 缓存 tool_use 的 input_json_delta，在 content_block_stop 时合并发出
    - 处理跨帧的 event/data 分离
    """
    output_chunks: List[bytes] = []
    chunk_count = 0

    if not frame_lines:
        return output_chunks, pending_event_name

    event_name: str | None = None
    data_lines: List[str] = []
    for frame_line in frame_lines:
        if frame_line.startswith("event:"):
            if event_name is None:
                event_name = frame_line[len("event:"):].strip()
        elif frame_line.startswith("data:"):
            data_value = frame_line[len("data:"):]
            if data_value.startswith(" "):
                data_value = data_value[1:]
            data_lines.append(data_value)

    # 处理跨帧的 event/data 分离
    if event_name and not data_lines:
        return output_chunks, event_name
    if not event_name and pending_event_name and data_lines:
        event_name = pending_event_name
        pending_event_name = None
    if event_name and pending_event_name and data_lines:
        pending_event_name = None

    if not data_lines:
        output_chunks.append(("\n".join(frame_lines) + "\n\n").encode("utf-8"))
        return output_chunks, pending_event_name

    payload = "\n".join(data_lines)
    if payload == "[DONE]":
        output_chunks.append(emit_frame(event_name, payload))
        return output_chunks, pending_event_name

    try:
        parsed = json.loads(payload)
    except Exception as parse_exc:
        print(f"[gateway malformed sse data] {type(parse_exc).__name__}: {parse_exc}; payload_len={len(payload)}")
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
        output_chunks.append(emit_frame("error", safe_error))
        return output_chunks, pending_event_name

    event_type = parsed.get("type") if isinstance(parsed, dict) else None
    event_index = parsed.get("index") if isinstance(parsed, dict) else None

    # 补全缺失的 content_block_start
    if (
        event_type in {"content_block_delta", "content_block_stop"}
        and isinstance(event_index, int)
        and event_index not in seen_block_starts
    ):
        synthetic_block = infer_synthetic_block_from_event(event_type, parsed)
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
        output_chunks.append(emit_frame("content_block_start", json.dumps(synthetic_payload, ensure_ascii=False)))
        seen_block_starts.add(event_index)
        block_kind_by_index[event_index] = str(synthetic_block.get("type", "text"))
    elif event_type == "content_block_start" and isinstance(event_index, int):
        seen_block_starts.add(event_index)
        if isinstance(parsed.get("content_block"), dict):
            cb_type = parsed["content_block"].get("type")
            if isinstance(cb_type, str):
                block_kind_by_index[event_index] = cb_type

    # 缓存 tool_use 的 input_json_delta，延迟到 content_block_stop 时合并
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
                return output_chunks, pending_event_name

    # content_block_stop 时发出合并后的 tool input JSON
    if (
        event_type == "content_block_stop"
        and isinstance(event_index, int)
        and block_kind_by_index.get(event_index) == "tool_use"
        and event_index in tool_partial_json_by_index
    ):
        normalized_json, normalize_reason = normalize_input_json_for_stream(
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
        output_chunks.append(emit_frame("content_block_delta", json.dumps(shim_delta_payload, ensure_ascii=False)))
        tool_partial_json_by_index.pop(event_index, None)

    output_chunks.append(emit_frame(event_name, payload))
    return output_chunks, pending_event_name
