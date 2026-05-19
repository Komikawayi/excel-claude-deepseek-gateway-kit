from datetime import datetime, timezone
from typing import Any, Dict

from claude_gateway.providers import ProviderConfig


def build_models_response(provider: ProviderConfig) -> Dict[str, Any]:
    """构建 /v1/models 响应，优先兼容严格客户端的模型发现逻辑。"""
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat().replace("+00:00", "Z")
    now_unix = int(now_dt.timestamp())
    default_capabilities = {
        "batch": {"supported": False},
        "citations": {"supported": False},
        "code_execution": {"supported": False},
        "context_management": {"supported": False},
        "effort": {"supported": False},
        "image_input": {"supported": provider.image_support},
        "pdf_input": {"supported": False},
        "structured_outputs": {"supported": True},
        "thinking": {"supported": False},
    }

    def _non_empty(value: str, fallback: str) -> str:
        text = (value or "").strip()
        return text if text else fallback

    def _model_info(model_id: str, display_name: str) -> Dict[str, Any]:
        return {
            "type": "model",
            "object": "model",
            "id": model_id,
            "name": display_name,
            "display_name": display_name,
            "created_at": now_iso,
            "created": now_unix,
            "max_input_tokens": provider.discovery_max_input_tokens,
            "max_tokens": provider.discovery_max_tokens,
            "context_window": provider.discovery_max_input_tokens,
            "max_output_tokens": provider.discovery_max_tokens,
            "capabilities": default_capabilities,
        }

    # 仅暴露 Opus / Sonnet 两档，避免客户端出现不需要的 Haiku 选项。
    ordered_ids = [
        (_non_empty(provider.alias_opus_versioned, "claude-opus-4-5"), "Claude Opus 4.5"),
        (_non_empty(provider.alias_sonnet_versioned, "claude-sonnet-4-5"), "Claude Sonnet 4.5"),
        (_non_empty(provider.alias_opus, "opus"), "Claude Opus 4.5"),
        (_non_empty(provider.alias_sonnet, "sonnet"), "Claude Sonnet 4.5"),
    ]

    seen_ids = set()
    model_data = []
    for model_id, display_name in ordered_ids:
        if model_id in seen_ids:
            continue
        seen_ids.add(model_id)
        model_data.append(_model_info(model_id, display_name))

    first_id = model_data[0]["id"] if model_data else ""
    last_id = model_data[-1]["id"] if model_data else ""

    return {
        "object": "list",
        "data": model_data,
        "first_id": first_id,
        "last_id": last_id,
        "has_more": False,
    }
