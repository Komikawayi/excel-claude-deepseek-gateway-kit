import hashlib
import json
import os
import re
from typing import Any, Dict, List

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from claude_gateway.log_mw import RequestLogMiddleware
from claude_gateway.models import build_models_response
from claude_gateway.providers import ProviderConfig, load_provider
from claude_gateway.sanitize import sanitize_request_body
from claude_gateway.stream import emit_frame, process_sse_frame
from claude_gateway.web_search import format_web_search_tool_result_text, search_duckduckgo_html

# 加载 .env（不覆盖已有的环境变量，方便生产环境通过 env 注入密钥）
load_dotenv(override=False)

# 加载 provider 配置
provider: ProviderConfig = load_provider()

_ALLOWED_ORIGINS_RAW = os.getenv("ALLOWED_ORIGIN", "").strip()
if _ALLOWED_ORIGINS_RAW == "*":
    ALLOWED_ORIGINS = ["*"]
elif _ALLOWED_ORIGINS_RAW:
    ALLOWED_ORIGINS = [item.strip() for item in _ALLOWED_ORIGINS_RAW.split(",") if item.strip()]
else:
    ALLOWED_ORIGINS = [
        "https://pivot.claude.ai",
        "null",
        "https://localhost",
        "https://localhost:3000",
        "https://localhost:5173",
        "https://appsource.microsoft.com",
        "https://store.office.com",
    ]

def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


MAX_REQUEST_BODY_BYTES = _int_env("MAX_REQUEST_BODY_BYTES", 4 * 1024 * 1024)  # 4MB
ENABLE_WEB_SEARCH_TOOL = _bool_env("ENABLE_WEB_SEARCH_TOOL", False)
ENABLE_AUTO_WEB_SEARCH_EXECUTION = _bool_env("ENABLE_AUTO_WEB_SEARCH_EXECUTION", True)
AUTO_WEB_SEARCH_MAX_RESULTS = _int_env("AUTO_WEB_SEARCH_MAX_RESULTS", 5)
AUTO_WEB_SEARCH_TIMEOUT_SECONDS = _float_env("AUTO_WEB_SEARCH_TIMEOUT_SECONDS", 20.0)
AUTO_WEB_SEARCH_MAX_ROUNDS = _int_env("AUTO_WEB_SEARCH_MAX_ROUNDS", 2)

app = FastAPI(
    title=f"Excel Claude -> {provider.name.title()} Gateway (Unified)",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(RequestLogMiddleware)


def _build_request_shape_summary(raw_body: Dict[str, Any], routed_model: str) -> Dict[str, Any]:
    """构建请求形状摘要用于日志。"""
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


def _request_fingerprint(summary: Dict[str, Any]) -> str:
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


def _extract_error_payload_from_text(text: str, *, expose_detail: bool = False) -> Dict[str, Any]:
    """解析上游错误响应。expose_detail=False 时对客户端泛化错误信息。"""
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            if not expose_detail:
                # 保留 error.type，但截断可能含内部信息的 message
                err = payload.get("error")
                if isinstance(err, dict):
                    return {"error": {"type": err.get("type", "upstream_error"), "message": "Upstream service error"}}
            return payload
    except ValueError:
        pass
    if expose_detail:
        return {"error": {"type": "upstream_non_json_error", "message": text[:8000]}}
    return {"error": {"type": "upstream_error", "message": "Upstream service error"}}


def _extract_web_search_allowed_domains(payload: Dict[str, Any]) -> list[str]:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = tool.get("type")
        if not (isinstance(tool_type, str) and tool_type.startswith("web_search_")):
            continue
        domains = tool.get("allowed_domains")
        if isinstance(domains, list):
            out = [str(item).strip() for item in domains if str(item).strip()]
            if out:
                return out
    return []


def _rewrite_web_search_tools_to_client_mode(payload: Dict[str, Any]) -> Dict[str, Any]:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return payload

    rewritten: list[Dict[str, Any]] = []
    changed = False
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = tool.get("type")
        if isinstance(tool_type, str) and tool_type.startswith("web_search_"):
            changed = True
            rewritten.append(
                {
                    "name": "web_search",
                    "description": "Search the web and return relevant results with URLs.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }
            )
        else:
            rewritten.append(tool)

    if not changed:
        return payload

    out = dict(payload)
    out["tools"] = rewritten
    return out


def _extract_web_search_tool_use_blocks(payload: Dict[str, Any]) -> list[Dict[str, Any]]:
    content = payload.get("content")
    if not isinstance(content, list):
        return []
    out: list[Dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue
        if block.get("name") != "web_search":
            continue
        block_id = block.get("id")
        if not isinstance(block_id, str) or not block_id:
            continue
        tool_input = block.get("input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        out.append({"id": block_id, "input": tool_input, "_synthetic": False})
    if out:
        return out

    # 兼容某些上游仅返回 <tool_call> XML 文本而非标准 tool_use block
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if not isinstance(text, str):
            continue
        if "<function=web_search>" not in text:
            continue

        query = ""
        marker_start = "<parameter=query>"
        marker_end = "</parameter>"
        s = text.find(marker_start)
        if s >= 0:
            e = text.find(marker_end, s + len(marker_start))
            if e > s:
                query = text[s + len(marker_start): e].strip()
        out.append(
            {
                "id": f"fallback_web_search_{len(out) + 1}",
                "input": {"query": query},
                "_synthetic": True,
            }
        )
    return out


def _build_assistant_tool_use_content_for_followup(
    payload: Dict[str, Any],
    tool_uses: list[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    if any(bool(item.get("_synthetic")) for item in tool_uses):
        preserved_reasoning: list[Dict[str, Any]] = []
        content = payload.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                # thinking/redacted_thinking 必须原样回传，避免上游 400（thinking mode 连续对话约束）
                if block_type in {"thinking", "redacted_thinking"}:
                    preserved_reasoning.append(block)

        synthetic_tool_use = [
            {
                "type": "tool_use",
                "id": str(item["id"]),
                "name": "web_search",
                "input": item.get("input") if isinstance(item.get("input"), dict) else {},
            }
            for item in tool_uses
            if str(item.get("id", "")).strip()
        ]
        return preserved_reasoning + synthetic_tool_use

    content = payload.get("content")
    return content if isinstance(content, list) else []


def _response_contains_xml_tool_call(payload: Dict[str, Any]) -> bool:
    content = payload.get("content")
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and "<tool_call>" in text and "<function=web_search>" in text:
            return True
    return False


def _build_final_answer_from_tool_results(tool_results: list[Dict[str, Any]]) -> str:
    urls: list[str] = []
    for block in tool_results:
        content = block.get("content")
        if not isinstance(content, str):
            continue
        for match in re.findall(r"https?://[^\s\])>]+", content):
            if match not in urls:
                urls.append(match)

    if urls:
        lines = ["已完成联网检索，以下是可用来源："]
        for idx, url in enumerate(urls[:8], 1):
            lines.append(f"{idx}. {url}")
        return "\n".join(lines)

    # 没有解析到 URL 时，回退展示最后一次 tool_result 摘要，避免把 <tool_call> 漏回客户端。
    if tool_results:
        content = tool_results[-1].get("content")
        if isinstance(content, str) and content.strip():
            return f"已完成联网检索，结果摘要：\n{content[:1200]}"

    return "已完成联网检索，但未获取到可展示的来源链接。"


async def _build_auto_web_search_tool_results(
    tool_uses: list[Dict[str, Any]],
    *,
    allowed_domains: list[str],
    fallback_query: str,
) -> list[Dict[str, Any]]:
    blocks: list[Dict[str, Any]] = []
    for tool_use in tool_uses:
        tool_use_id = str(tool_use.get("id", "")).strip()
        if not tool_use_id:
            continue
        tool_input = tool_use.get("input") if isinstance(tool_use.get("input"), dict) else {}
        query = str(tool_input.get("query", "")).strip()
        if not query:
            query = fallback_query.strip() or "latest news"
            print(
                "[gateway auto_web_search] empty_query_fallback "
                f"tool_use_id={tool_use_id} fallback_query={query!r}"
            )

        try:
            results = await search_duckduckgo_html(
                query,
                max_results=AUTO_WEB_SEARCH_MAX_RESULTS,
                timeout_s=AUTO_WEB_SEARCH_TIMEOUT_SECONDS,
                allowed_domains=allowed_domains,
            )
            content = format_web_search_tool_result_text(query, results)
        except Exception as exc:
            content = f"web search failed: {type(exc).__name__}"

        blocks.append(
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content,
            }
        )

    return blocks


def _extract_last_user_text(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            if parts:
                return " ".join(parts)
    return ""


async def _post_upstream_json(
    *,
    upstream_url: str,
    headers: Dict[str, str],
    body: Dict[str, Any],
    timeout: float = 60.0,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(upstream_url, headers=headers, json=body)


@app.get("/healthz")
async def healthz() -> Dict[str, str]:
    return {"status": "ok", "provider": provider.name}


@app.get("/v1/models")
async def list_models() -> Dict[str, Any]:
    return build_models_response(provider)


@app.get("/models")
async def list_models_alias() -> Dict[str, Any]:
    return await list_models()


@app.post("/v1/messages")
async def create_message(req: Request):
    # 请求体大小限制（content-length 快速拒绝 + 实际读取兜底）
    content_length = req.headers.get("content-length")
    if content_length:
        try:
            cl = int(content_length)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header")
        if cl > MAX_REQUEST_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Request body too large")

    # 解析上游路由（仅读 header，不影响 body 流）
    upstream_key, upstream_url, route_kind = provider.resolve_upstream_url(req)
    print(f"[gateway route] kind={route_kind} provider={provider.name}")

    # 逐块读取 body，限制实际字节数（防止 content-length 伪造或缺失）
    body_chunks: list[bytes] = []
    total_bytes = 0
    async for chunk in req.stream():
        body_chunks.append(chunk)
        total_bytes += len(chunk)
        if total_bytes > MAX_REQUEST_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Request body too large")

    raw_bytes = b"".join(body_chunks)
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Empty request body")

    try:
        raw_body = json.loads(raw_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    if not isinstance(raw_body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")

    # 根据实际路由决定图片支持（auto 模式下按请求动态决定）
    image_support = provider.resolve_image_support(route_kind)

    # 消毒请求体
    body, dropped, removed_fields = sanitize_request_body(
        raw_body,
        image_support=image_support,
        passthrough_metadata=provider.passthrough_metadata,
        enable_web_search_tool=ENABLE_WEB_SEARCH_TOOL,
        normalize_web_search_as_client_tool=ENABLE_AUTO_WEB_SEARCH_EXECUTION,
        default_max_tokens=provider.default_max_tokens,
        min_compat_max_tokens=provider.min_compat_max_tokens,
    )
    web_search_allowed_domains = _extract_web_search_allowed_domains(body)
    if ENABLE_WEB_SEARCH_TOOL and ENABLE_AUTO_WEB_SEARCH_EXECUTION:
        body = _rewrite_web_search_tools_to_client_mode(body)

    # 模型路由
    body["model"] = provider.route_model(str(body.get("model", "")), route_kind)

    req_summary = _build_request_shape_summary(raw_body, body["model"])
    req_fp = _request_fingerprint(req_summary)
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

    headers = {
        "Authorization": f"Bearer {upstream_key}",
        "x-api-key": upstream_key,
        "anthropic-version": req.headers.get("anthropic-version", "2023-06-01"),
        "content-type": "application/json",
    }

    try:
        if bool(body.get("stream", False)):
            # 流式模式
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
                    frame_buffer: List[str] = []
                    async for raw_line in upstream_stream.aiter_lines():
                        if raw_line is None:
                            continue
                        if isinstance(raw_line, bytes):
                            raw_line = raw_line.decode("utf-8", errors="replace")

                        if raw_line == "":
                            chunks, pending_event_name = await process_sse_frame(
                                frame_buffer,
                                seen_block_starts,
                                block_kind_by_index,
                                tool_partial_json_by_index,
                                pending_event_name,
                            )
                            for chunk in chunks:
                                chunk_count += 1
                                yield chunk
                            frame_buffer = []
                            continue

                        frame_buffer.append(raw_line)

                    if frame_buffer:
                        chunks, _ = await process_sse_frame(
                            frame_buffer,
                            seen_block_starts,
                            block_kind_by_index,
                            tool_partial_json_by_index,
                            pending_event_name,
                        )
                        for chunk in chunks:
                            chunk_count += 1
                            yield chunk
                except Exception as exc:
                    print(f"[gateway stream error] {type(exc).__name__}: {exc}")
                    safe_error = json.dumps(
                        {
                            "type": "error",
                            "error": {
                                "type": "gateway_stream_error",
                                "message": "Upstream stream interrupted",
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

        # 非流式模式
        upstream = await _post_upstream_json(upstream_url=upstream_url, headers=headers, body=body, timeout=60.0)
    except httpx.HTTPError as exc:
        err_type = type(exc).__name__
        err_msg = str(exc)
        print(f"[gateway upstream http error] type={err_type} message={err_msg}")
        raise HTTPException(
            status_code=502,
            detail={"type": "upstream_http_error", "message": "Upstream service unavailable"},
        ) from exc

    if upstream.status_code >= 400:
        # 服务端日志记录完整错误，客户端只看泛化信息
        payload = _extract_error_payload_from_text(upstream.text, expose_detail=False)
        print(f"[gateway upstream error] status={upstream.status_code} body={upstream.text[:500]}")
        return JSONResponse(status_code=upstream.status_code, content=payload)

    final_status_code = upstream.status_code
    try:
        payload = upstream.json()
    except ValueError:
        payload = {"error": {"type": "upstream_non_json_error", "message": "Invalid response from upstream"}}

    # 非流式自动工具回路：只要能从响应中提取出 web_search tool call，就执行回路。
    auto_tool_uses: list[Dict[str, Any]] = []
    if (
        ENABLE_WEB_SEARCH_TOOL
        and ENABLE_AUTO_WEB_SEARCH_EXECUTION
        and not bool(body.get("stream", False))
        and isinstance(payload, dict)
    ):
        auto_tool_uses = _extract_web_search_tool_use_blocks(payload)
        if auto_tool_uses and payload.get("stop_reason") != "tool_use":
            print(
                "[gateway auto_web_search] pseudo_tool_call_end_turn "
                f"stop_reason={payload.get('stop_reason')}"
            )

    if auto_tool_uses:
        tool_uses = auto_tool_uses
        if tool_uses:
            fallback_query = _extract_last_user_text(body.get("messages"))
            followup_messages = list(body.get("messages", []))
            last_tool_results: list[Dict[str, Any]] = []

            for round_idx in range(max(1, AUTO_WEB_SEARCH_MAX_ROUNDS)):
                tool_results = await _build_auto_web_search_tool_results(
                    tool_uses,
                    allowed_domains=web_search_allowed_domains,
                    fallback_query=fallback_query,
                )
                if not tool_results:
                    break
                last_tool_results = tool_results

                assistant_content = _build_assistant_tool_use_content_for_followup(payload, tool_uses)
                if not assistant_content:
                    break
                followup_messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_content,
                    }
                )
                followup_messages.append(
                    {
                        "role": "user",
                        "content": tool_results,
                    }
                )
                followup_body = dict(body)
                followup_body["messages"] = followup_messages
                # 方案 C：从首个有效 tool_result 开始，后续请求禁止继续 tool_use。
                if round_idx >= 0:
                    followup_body.pop("tools", None)
                    followup_body["tool_choice"] = {"type": "none"}
                    system_value = followup_body.get("system")
                    tool_guard = (
                        "You already have web search results. "
                        "Do not call tools again. Produce final answer with URLs."
                    )
                    if isinstance(system_value, str) and system_value.strip():
                        followup_body["system"] = system_value + "\n\n" + tool_guard
                    elif isinstance(system_value, list):
                        followup_body["system"] = list(system_value) + [{"type": "text", "text": tool_guard}]
                    else:
                        followup_body["system"] = tool_guard

                try:
                    upstream2 = await _post_upstream_json(
                        upstream_url=upstream_url,
                        headers=headers,
                        body=followup_body,
                        timeout=60.0,
                    )
                except httpx.HTTPError as exc:
                    err_type = type(exc).__name__
                    err_msg = str(exc)
                    print(f"[gateway upstream http error] type={err_type} message={err_msg}")
                    raise HTTPException(
                        status_code=502,
                        detail={"type": "upstream_http_error", "message": "Upstream service unavailable"},
                    ) from exc

                if upstream2.status_code >= 400:
                    payload2 = _extract_error_payload_from_text(upstream2.text, expose_detail=False)
                    print(f"[gateway upstream error] status={upstream2.status_code} body={upstream2.text[:500]}")
                    return JSONResponse(status_code=upstream2.status_code, content=payload2)

                final_status_code = upstream2.status_code
                try:
                    payload = upstream2.json()
                except ValueError:
                    payload = {
                        "error": {
                            "type": "upstream_non_json_error",
                            "message": "Invalid response from upstream",
                        }
                    }
                    break

                if not isinstance(payload, dict):
                    break
                tool_uses = _extract_web_search_tool_use_blocks(payload)
                has_xml_tool_call = _response_contains_xml_tool_call(payload)
                if not tool_uses:
                    break
                if payload.get("stop_reason") != "tool_use":
                    print(
                        "[gateway auto_web_search] pseudo_tool_call_followup "
                        f"round={round_idx + 1} stop_reason={payload.get('stop_reason')}"
                    )
                if has_xml_tool_call:
                    print(
                        "[gateway auto_web_search] xml_tool_call_followup "
                        f"round={round_idx + 1}"
                    )
                    # 继续下一轮，让 XML tool_call 有机会被吞入回路，不直接漏回客户端。
                    continue

            # 兜底：已执行过本地工具回路，但最终仍是 XML tool_call，则返回本地聚合答案，避免漏回 <tool_call>。
            if isinstance(payload, dict) and _response_contains_xml_tool_call(payload) and last_tool_results:
                fallback_text = _build_final_answer_from_tool_results(last_tool_results)
                payload = {
                    "id": payload.get("id", "gateway_fallback"),
                    "type": "message",
                    "role": "assistant",
                    "model": payload.get("model", body.get("model", "")),
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": fallback_text}],
                }

    return JSONResponse(status_code=final_status_code, content=payload)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def fallback(path: str) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "Not found"})


def cli():
    """命令行入口：claude-gateway --provider auto --port 8790"""
    import argparse
    import subprocess
    import sys

    parser = argparse.ArgumentParser(description="Claude-compatible gateway")
    parser.add_argument(
        "--provider",
        choices=["deepseek", "kimi", "mimo", "auto"],
        default=os.getenv("ACTIVE_PROVIDER", "auto"),
    )
    parser.add_argument("--port", type=int, default=int(os.getenv("GATEWAY_PORT", "8790")))
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    # 注意：console script 会先 import 本模块，再调用 cli()。
    # 若在当前进程直接 uvicorn.run("claude_gateway.main:app")，
    # provider 可能在 import 阶段已按旧环境变量初始化，导致 --provider 不生效。
    # 这里用子进程重新启动 uvicorn，并显式注入 ACTIVE_PROVIDER，确保参数生效。
    child_env = os.environ.copy()
    child_env["ACTIVE_PROVIDER"] = args.provider
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "claude_gateway.main:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    result = subprocess.run(cmd, env=child_env, check=False)
    return int(result.returncode)
