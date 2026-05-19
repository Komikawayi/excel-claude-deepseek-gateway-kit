import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest
from fastapi.testclient import TestClient

THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _build_base_body(stream: bool = False) -> Dict[str, Any]:
    return {
        "model": "claude-sonnet-4-5",
        "max_tokens": 32,
        "stream": stream,
        "messages": [{"role": "user", "content": "hello"}],
    }


def _build_web_search_body(stream: bool = False) -> Dict[str, Any]:
    return {
        "model": "claude-sonnet-4-5",
        "max_tokens": 64,
        "stream": stream,
        "tools": [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3,
                "allowed_domains": ["example.com"],
            }
        ],
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "server_tool_use",
                        "id": "srvtoolu_123",
                        "name": "web_search",
                        "input": {"query": "latest ai news"},
                    },
                    {
                        "type": "web_search_tool_result",
                        "tool_use_id": "srvtoolu_123",
                        "content": [
                            {
                                "type": "web_search_result",
                                "title": "AI News",
                                "url": "https://example.com/news",
                                "encrypted_content": "opaque",
                            }
                        ],
                    },
                ],
            },
            {"role": "user", "content": "continue"},
        ],
    }


def _build_web_search_user_body(stream: bool = False) -> Dict[str, Any]:
    return {
        "model": "claude-sonnet-4-5",
        "max_tokens": 64,
        "stream": stream,
        "tools": [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 2,
                "allowed_domains": ["example.com"],
            }
        ],
        "messages": [
            {"role": "user", "content": "查一下今天北京天气并给链接"},
        ],
    }


def _reload_main(monkeypatch: pytest.MonkeyPatch, env_overrides: Dict[str, str | None]):
    # 先清除所有 provider 相关的环境变量，防止测试间泄漏
    _provider_env_keys = [
        "ACTIVE_PROVIDER", "GATEWAY_PORT", "ALLOWED_ORIGIN",
        "DEFAULT_MAX_TOKENS", "MIN_COMPAT_MAX_TOKENS",
        "MODEL_PRIMARY", "MODEL_MID", "MODEL_FAST",
        "DEEPSEEK_MODEL_PRIMARY", "DEEPSEEK_MODEL_MID", "DEEPSEEK_MODEL_FAST",
        "KIMI_MODEL_PRIMARY", "KIMI_MODEL_MID", "KIMI_MODEL_FAST",
        "MIMO_MODEL_PRIMARY", "MIMO_MODEL_MID", "MIMO_MODEL_FAST",
        "MINIMAX_MODEL_PRIMARY", "MINIMAX_MODEL_MID", "MINIMAX_MODEL_FAST",
        "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL",
        "KIMI_API_KEY", "UPSTREAM_API_KEY",
        "KIMI_CODING_BASE_URL", "CODINGPLAN_BASE_URL",
        "KIMI_PAYG_BASE_URL", "PAYG_BASE_URL",
        "CODINGPLAN_MODEL",
        "MIMO_API_KEY", "MIMO_PAYG_BASE_URL",
        "MIMO_TP_REGION", "MIMO_TP_BASE_URL_CN",
        "MIMO_TP_BASE_URL_SGP", "MIMO_TP_BASE_URL_AMS",
        "MINIMAX_API_KEY", "MINIMAX_REGION",
        "MINIMAX_BASE_URL_CN", "MINIMAX_BASE_URL_GLOBAL",
        "GATEWAY_PASSTHROUGH_METADATA",
        "ALIAS_SONNET", "ALIAS_SONNET_VERSIONED",
        "ALIAS_OPUS", "ALIAS_OPUS_VERSIONED",
        "ALIAS_HAIKU", "ALIAS_HAIKU_VERSIONED",
        "DISCOVERY_MAX_INPUT_TOKENS", "DISCOVERY_MAX_TOKENS",
        "LOG_CONTENT_REDACT", "LOG_CONTENT_MAX_CHARS",
        "ENABLE_WEB_SEARCH_TOOL",
        "ENABLE_AUTO_WEB_SEARCH_EXECUTION",
        "AUTO_WEB_SEARCH_MAX_RESULTS",
        "AUTO_WEB_SEARCH_TIMEOUT_SECONDS",
    ]
    for key in _provider_env_keys:
        # 用 setenv("") 而非 delenv，防止 load_dotenv(override=False) 从 .env 重新加载
        monkeypatch.setenv(key, "")

    for key, value in env_overrides.items():
        monkeypatch.setenv(key, value if value is not None else "")

    # 清除已加载的模块以重新加载
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("claude_gateway."):
            del sys.modules[mod_name]

    module = importlib.import_module("claude_gateway.main")
    return module


def _wire_async_client(monkeypatch: pytest.MonkeyPatch, module, handler):
    class BoundAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", BoundAsyncClient)


def test_cli_provider_passed_to_child_env(monkeypatch):
    """CLI 应将 --provider 写入子进程环境，避免 import 时 provider 固化。"""
    import subprocess

    monkeypatch.setenv("ACTIVE_PROVIDER", "deepseek")
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("claude_gateway."):
            del sys.modules[mod_name]
    module = importlib.import_module("claude_gateway.main")

    captured: Dict[str, Any] = {}

    def fake_run(cmd, env=None, check=False):
        captured["cmd"] = cmd
        captured["env"] = env or {}
        captured["check"] = check

        class _Result:
            returncode = 0
        return _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["claude-gateway", "--provider", "mimo", "--port", "8797", "--host", "127.0.0.1"])

    rc = module.cli()
    assert rc == 0
    assert captured["env"]["ACTIVE_PROVIDER"] == "mimo"
    assert captured["cmd"][:4] == [sys.executable, "-m", "uvicorn", "claude_gateway.main:app"]


class TestDeepSeekProvider:
    def test_basic_routing(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test-key",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/anthropic",
            "MODEL_PRIMARY": "deepseek-v4-pro",
            "MODEL_FAST": "deepseek-v4-flash",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", json=_build_base_body())
        assert response.status_code == 200
        assert captured["url"] == "https://api.deepseek.com/anthropic/v1/messages"
        assert captured["auth"] == "Bearer sk-test-key"

    def test_model_mapping(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured["model"] = body["model"]
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/anthropic",
            "MODEL_PRIMARY": "deepseek-v4-pro",
            "MODEL_FAST": "deepseek-v4-flash",
            "MODEL_MID": "deepseek-v4-flash",
            "ALIAS_HAIKU_VERSIONED": "claude-haiku-4-5",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        # Haiku 兼容映射到 sonnet(mid) 档
        body = _build_base_body()
        body["model"] = "claude-haiku-4-5"
        client.post("/v1/messages", json=body)
        assert captured["model"] == "deepseek-v4-flash"

    def test_model_prefix_matching(self, monkeypatch):
        """新版 Claude 别名（如 claude-sonnet-4-6）应通过前缀匹配映射到正确档位。"""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured["model"] = body["model"]
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/anthropic",
            "MODEL_PRIMARY": "deepseek-v4-pro",
            "MODEL_FAST": "deepseek-v4-flash",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        # claude-sonnet-4-6 → mid（前缀 claude-sonnet*）
        body = _build_base_body()
        body["model"] = "claude-sonnet-4-6"
        client.post("/v1/messages", json=body)
        assert captured["model"] == "deepseek-v4-flash"  # mid 回退到 fast

        # claude-haiku-4-6 → mid（前缀 claude-haiku* 兼容映射到 sonnet 档）
        body["model"] = "claude-haiku-4-6"
        client.post("/v1/messages", json=body)
        assert captured["model"] == "deepseek-v4-flash"

        # claude-opus-5 → primary（前缀 claude-opus*）
        body["model"] = "claude-opus-5"
        client.post("/v1/messages", json=body)
        assert captured["model"] == "deepseek-v4-pro"

    def test_missing_key_returns_401(self, monkeypatch):
        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "",
        })
        client = TestClient(module.app)
        response = client.post("/v1/messages", json=_build_base_body())
        assert response.status_code == 401

    def test_incoming_non_sk_key_returns_401(self, monkeypatch):
        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/anthropic",
        })
        client = TestClient(module.app)
        response = client.post("/v1/messages", headers={"x-api-key": "dk-test-key"}, json=_build_base_body())
        assert response.status_code == 401


class TestKimiProvider:
    def test_sk_kimi_routes_to_coding(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            body = json.loads(request.content)
            captured["model"] = body["model"]
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "kimi",
            "KIMI_API_KEY": "",
            "KIMI_CODING_BASE_URL": "https://api.kimi.com/coding",
            "KIMI_PAYG_BASE_URL": "https://api.moonshot.cn/anthropic",
            "MODEL_PRIMARY": "kimi-k2.6",
            "CODINGPLAN_MODEL": "kimi-for-coding",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", headers={"x-api-key": "sk-kimi-123"}, json=_build_base_body())
        assert response.status_code == 200
        assert captured["url"] == "https://api.kimi.com/coding/v1/messages"
        assert captured["model"] == "kimi-for-coding"

    def test_sk_routes_to_payg(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "kimi",
            "KIMI_API_KEY": "",
            "KIMI_CODING_BASE_URL": "https://api.kimi.com/coding",
            "KIMI_PAYG_BASE_URL": "https://api.moonshot.cn/anthropic",
            "MODEL_PRIMARY": "kimi-k2.6",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", headers={"x-api-key": "sk-payg-456"}, json=_build_base_body())
        assert response.status_code == 200
        assert captured["url"] == "https://api.moonshot.cn/anthropic/v1/messages"

    def test_image_support(self, monkeypatch):
        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "kimi",
            "KIMI_API_KEY": "sk-test",
        })
        assert module.provider.image_support is True


class TestMiMoProvider:
    def test_sk_routes_to_payg(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "mimo",
            "MIMO_API_KEY": "",
            "MIMO_PAYG_BASE_URL": "https://api.xiaomimimo.com/anthropic",
            "MIMO_TP_BASE_URL_CN": "https://tp-cn.example/anthropic",
            "MODEL_PRIMARY": "mimo-v2.5",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", headers={"x-api-key": "sk-test-key"}, json=_build_base_body())
        assert response.status_code == 200
        assert captured["url"] == "https://api.xiaomimimo.com/anthropic/v1/messages"

    def test_tp_routes_to_default_cn(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "mimo",
            "MIMO_API_KEY": "",
            "MIMO_TP_REGION": "cn",
            "MIMO_PAYG_BASE_URL": "https://payg.example/anthropic",
            "MIMO_TP_BASE_URL_CN": "https://tp-cn.example/anthropic",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", headers={"x-api-key": "tp-test-key"}, json=_build_base_body())
        assert response.status_code == 200
        assert captured["url"] == "https://tp-cn.example/anthropic/v1/messages"

    def test_region_override(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "mimo",
            "MIMO_API_KEY": "",
            "MIMO_TP_REGION": "cn",
            "MIMO_TP_BASE_URL_CN": "https://tp-cn.example/anthropic",
            "MIMO_TP_BASE_URL_SGP": "https://tp-sgp.example/anthropic",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post(
            "/v1/messages",
            headers={"x-api-key": "tp-test-key", "x-mimo-tp-region": "sgp"},
            json=_build_base_body(),
        )
        assert response.status_code == 200
        assert captured["url"] == "https://tp-sgp.example/anthropic/v1/messages"

    def test_invalid_region_returns_400(self, monkeypatch):
        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "mimo",
            "MIMO_API_KEY": "",
            "MIMO_TP_REGION": "cn",
        })
        client = TestClient(module.app)
        response = client.post(
            "/v1/messages",
            headers={"x-api-key": "tp-test-key", "x-mimo-tp-region": "moon"},
            json=_build_base_body(),
        )
        assert response.status_code == 400

    def test_invalid_key_prefix_returns_401(self, monkeypatch):
        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "mimo",
            "MIMO_API_KEY": "",
        })
        client = TestClient(module.app)
        response = client.post("/v1/messages", headers={"x-api-key": "bad-prefix"}, json=_build_base_body())
        assert response.status_code == 401


class TestMiniMaxProvider:
    def test_sk_api_routes_to_payg(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            body = json.loads(request.content)
            captured["model"] = body["model"]
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "minimax",
            "MINIMAX_API_KEY": "",
            "MINIMAX_REGION": "cn",
            "MINIMAX_BASE_URL_CN": "https://api.minimaxi.com/anthropic",
            "MODEL_PRIMARY": "MiniMax-M2.7",
            "MODEL_MID": "MiniMax-M2.5",
            "MODEL_FAST": "MiniMax-M2.5-highspeed",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        body = _build_base_body()
        body["model"] = "claude-opus-4-5"
        response = client.post("/v1/messages", headers={"x-api-key": "sk-api-123"}, json=body)
        assert response.status_code == 200
        assert captured["url"] == "https://api.minimaxi.com/anthropic/v1/messages"
        assert captured["model"] == "MiniMax-M2.7"

    def test_sk_cp_routes_to_codingplan(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            body = json.loads(request.content)
            captured["model"] = body["model"]
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "minimax",
            "MINIMAX_API_KEY": "",
            "MINIMAX_REGION": "cn",
            "MINIMAX_BASE_URL_CN": "https://api.minimaxi.com/anthropic",
            "MODEL_PRIMARY": "MiniMax-M2.7",
            "MODEL_MID": "MiniMax-M2.5",
            "MODEL_FAST": "MiniMax-M2.5-highspeed",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        body = _build_base_body()
        body["model"] = "claude-sonnet-4-5"
        response = client.post("/v1/messages", headers={"x-api-key": "sk-cp-456"}, json=body)
        assert response.status_code == 200
        assert captured["url"] == "https://api.minimaxi.com/anthropic/v1/messages"
        assert captured["model"] == "MiniMax-M2.5"

    def test_haiku_maps_to_highspeed(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            body = json.loads(request.content)
            captured["model"] = body["model"]
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "minimax",
            "MINIMAX_API_KEY": "",
            "MINIMAX_BASE_URL_CN": "https://api.minimaxi.com/anthropic",
            "MODEL_PRIMARY": "MiniMax-M2.7",
            "MODEL_MID": "MiniMax-M2.5",
            "MODEL_FAST": "MiniMax-M2.5-highspeed",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        body = _build_base_body()
        body["model"] = "claude-haiku-4-5"
        response = client.post("/v1/messages", headers={"x-api-key": "sk-api-123"}, json=body)
        assert response.status_code == 200
        assert captured["url"] == "https://api.minimaxi.com/anthropic/v1/messages"
        assert captured["model"] == "MiniMax-M2.5-highspeed"

    def test_region_override_global(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "minimax",
            "MINIMAX_API_KEY": "",
            "MINIMAX_REGION": "cn",
            "MINIMAX_BASE_URL_CN": "https://api.minimaxi.com/anthropic",
            "MINIMAX_BASE_URL_GLOBAL": "https://api.minimax.io/anthropic",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post(
            "/v1/messages",
            headers={"x-api-key": "sk-cp-456", "x-minimax-region": "global"},
            json=_build_base_body(),
        )
        assert response.status_code == 200
        assert captured["url"] == "https://api.minimax.io/anthropic/v1/messages"

    def test_invalid_region_returns_400(self, monkeypatch):
        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "minimax",
            "MINIMAX_API_KEY": "",
            "MINIMAX_REGION": "cn",
        })
        client = TestClient(module.app)
        response = client.post(
            "/v1/messages",
            headers={"x-api-key": "sk-api-123", "x-minimax-region": "moon"},
            json=_build_base_body(),
        )
        assert response.status_code == 400

    def test_invalid_key_prefix_returns_401(self, monkeypatch):
        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "minimax",
            "MINIMAX_API_KEY": "",
        })
        client = TestClient(module.app)
        response = client.post("/v1/messages", headers={"x-api-key": "sk-legacy-123"}, json=_build_base_body())
        assert response.status_code == 401

    def test_upstream_429_passthrough(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": {"type": "rate_limit", "code": 1002, "message": "too fast"}})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "minimax",
            "MINIMAX_API_KEY": "sk-api-test",
            "MINIMAX_BASE_URL_CN": "https://api.minimaxi.com/anthropic",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", json=_build_base_body())
        assert response.status_code == 429
        body = response.json()
        assert body["error"]["type"] == "rate_limit"

    def test_upstream_402_passthrough_for_token_plan_quota(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(402, json={"error": {"type": "quota_exceeded", "code": 2056, "message": "quota"}})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "minimax",
            "MINIMAX_API_KEY": "",
            "MINIMAX_BASE_URL_CN": "https://api.minimaxi.com/anthropic",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", headers={"x-api-key": "sk-cp-456"}, json=_build_base_body())
        assert response.status_code == 402
        body = response.json()
        assert body["error"]["type"] == "quota_exceeded"


class TestAutoProvider:
    """测试 ACTIVE_PROVIDER=auto 自动路由模式。"""

    def test_dk_prefix_routes_to_deepseek(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "auto",
            "DEEPSEEK_BASE_URL": "https://ds.example/anthropic",
            "KIMI_CODING_BASE_URL": "https://kimi-coding.example",
            "KIMI_PAYG_BASE_URL": "https://kimi-payg.example",
            "MIMO_PAYG_BASE_URL": "https://mimo-payg.example",
            "MIMO_TP_BASE_URL_CN": "https://mimo-tp.example",
            "MODEL_PRIMARY": "mimo-v2.5",
            "MODEL_MID": "mimo-v2.5",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", headers={"x-api-key": "dk-test-key"}, json=_build_base_body())
        assert response.status_code == 200
        assert captured["url"] == "https://ds.example/anthropic/v1/messages"
        assert captured["auth"] == "Bearer dk-test-key"

    def test_sk_kimi_prefix_routes_to_kimi_coding(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            body = json.loads(request.content)
            captured["model"] = body["model"]
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "auto",
            "DEEPSEEK_BASE_URL": "https://ds.example/anthropic",
            "KIMI_CODING_BASE_URL": "https://kimi-coding.example",
            "KIMI_PAYG_BASE_URL": "https://kimi-payg.example",
            "MIMO_PAYG_BASE_URL": "https://mimo-payg.example",
            "MIMO_TP_BASE_URL_CN": "https://mimo-tp.example",
            "MODEL_PRIMARY": "mimo-v2.5",
            "MODEL_MID": "mimo-v2.5",
            "CODINGPLAN_MODEL": "kimi-for-coding",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", headers={"x-api-key": "sk-kimi-123"}, json=_build_base_body())
        assert response.status_code == 200
        assert captured["url"] == "https://kimi-coding.example/v1/messages"
        assert captured["model"] == "kimi-for-coding"

    def test_tp_prefix_routes_to_mimo_tp(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "auto",
            "DEEPSEEK_BASE_URL": "https://ds.example/anthropic",
            "KIMI_CODING_BASE_URL": "https://kimi-coding.example",
            "KIMI_PAYG_BASE_URL": "https://kimi-payg.example",
            "MIMO_PAYG_BASE_URL": "https://mimo-payg.example",
            "MIMO_TP_BASE_URL_CN": "https://mimo-tp-cn.example",
            "MIMO_TP_BASE_URL_SGP": "https://mimo-tp-sgp.example",
            "MIMO_TP_REGION": "cn",
            "MODEL_PRIMARY": "mimo-v2.5",
            "MODEL_MID": "mimo-v2.5",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", headers={"x-api-key": "tp-test-key"}, json=_build_base_body())
        assert response.status_code == 200
        assert captured["url"] == "https://mimo-tp-cn.example/v1/messages"

    def test_sk_mimo_prefix_routes_to_mimo_payg(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "auto",
            "DEEPSEEK_BASE_URL": "https://ds.example/anthropic",
            "KIMI_CODING_BASE_URL": "https://kimi-coding.example",
            "KIMI_PAYG_BASE_URL": "https://kimi-payg.example",
            "MIMO_PAYG_BASE_URL": "https://mimo-payg.example",
            "MIMO_TP_BASE_URL_CN": "https://mimo-tp.example",
            "MODEL_PRIMARY": "mimo-v2.5",
            "MODEL_MID": "mimo-v2.5",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", headers={"x-api-key": "sk-mimo-abc"}, json=_build_base_body())
        assert response.status_code == 200
        assert captured["url"] == "https://mimo-payg.example/v1/messages"

    def test_bare_sk_prefix_defaults_to_mimo_payg(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "auto",
            "DEEPSEEK_BASE_URL": "https://ds.example/anthropic",
            "KIMI_CODING_BASE_URL": "https://kimi-coding.example",
            "KIMI_PAYG_BASE_URL": "https://kimi-payg.example",
            "MIMO_PAYG_BASE_URL": "https://mimo-payg.example",
            "MIMO_TP_BASE_URL_CN": "https://mimo-tp.example",
            "MODEL_PRIMARY": "mimo-v2.5",
            "MODEL_MID": "mimo-v2.5",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", headers={"x-api-key": "sk-generic-123"}, json=_build_base_body())
        assert response.status_code == 200
        assert captured["url"] == "https://mimo-payg.example/v1/messages"

    def test_no_key_returns_401(self, monkeypatch):
        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "auto",
            "MIMO_PAYG_BASE_URL": "https://mimo-payg.example",
            "MIMO_TP_BASE_URL_CN": "https://mimo-tp.example",
            "MODEL_PRIMARY": "mimo-v2.5",
            "MODEL_MID": "mimo-v2.5",
        })
        client = TestClient(module.app)
        response = client.post("/v1/messages", json=_build_base_body())
        assert response.status_code == 401

    def test_tp_prefix_respects_region_override(self, monkeypatch):
        """auto 模式下 tp-* 请求应读取 x-mimo-tp-region 头进行区域覆写。"""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "auto",
            "DEEPSEEK_BASE_URL": "https://ds.example/anthropic",
            "KIMI_CODING_BASE_URL": "https://kimi-coding.example",
            "KIMI_PAYG_BASE_URL": "https://kimi-payg.example",
            "MIMO_PAYG_BASE_URL": "https://mimo-payg.example",
            "MIMO_TP_BASE_URL_CN": "https://mimo-tp-cn.example",
            "MIMO_TP_BASE_URL_SGP": "https://mimo-tp-sgp.example",
            "MIMO_TP_BASE_URL_AMS": "https://mimo-tp-ams.example",
            "MIMO_TP_REGION": "cn",
            "MODEL_PRIMARY": "mimo-v2.5",
            "MODEL_MID": "mimo-v2.5",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        # 默认区域（cn）
        response = client.post("/v1/messages", headers={"x-api-key": "tp-test-key"}, json=_build_base_body())
        assert response.status_code == 200
        assert captured["url"] == "https://mimo-tp-cn.example/v1/messages"

        # 覆写到 sgp
        response = client.post(
            "/v1/messages",
            headers={"x-api-key": "tp-test-key", "x-mimo-tp-region": "sgp"},
            json=_build_base_body(),
        )
        assert response.status_code == 200
        assert captured["url"] == "https://mimo-tp-sgp.example/v1/messages"

        # 覆写到 ams
        response = client.post(
            "/v1/messages",
            headers={"x-api-key": "tp-test-key", "x-mimo-tp-region": "ams"},
            json=_build_base_body(),
        )
        assert response.status_code == 200
        assert captured["url"] == "https://mimo-tp-ams.example/v1/messages"

    def test_tp_invalid_region_returns_400(self, monkeypatch):
        """auto 模式下无效区域头应返回 400。"""
        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "auto",
            "MIMO_PAYG_BASE_URL": "https://mimo-payg.example",
            "MIMO_TP_BASE_URL_CN": "https://mimo-tp.example",
            "MIMO_TP_REGION": "cn",
            "MODEL_PRIMARY": "mimo-v2.5",
            "MODEL_MID": "mimo-v2.5",
        })
        client = TestClient(module.app)
        response = client.post(
            "/v1/messages",
            headers={"x-api-key": "tp-test-key", "x-mimo-tp-region": "moon"},
            json=_build_base_body(),
        )
        assert response.status_code == 400

    def test_kimi_payg_unreachable_in_auto_mode(self, monkeypatch):
        """auto 模式下 sk-* 默认路由到 MiMo PAYG，Kimi PAYG 不可达（已知限制）。"""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured["model"] = body["model"]
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "auto",
            "DEEPSEEK_BASE_URL": "https://ds.example/anthropic",
            "KIMI_CODING_BASE_URL": "https://kimi-coding.example",
            "KIMI_PAYG_BASE_URL": "https://kimi-payg.example",
            "MIMO_PAYG_BASE_URL": "https://mimo-payg.example",
            "MIMO_TP_BASE_URL_CN": "https://mimo-tp.example",
            "MODEL_PRIMARY": "mimo-v2.5",
            "MODEL_MID": "mimo-v2.5",
            "KIMI_MODEL_PRIMARY": "kimi-k2.6",
            "KIMI_MODEL_MID": "kimi-k2.5",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        # sk-* 被路由到 MiMo PAYG（而非 Kimi PAYG），这是已知限制
        # claude-sonnet-4-5 → sonnet → model_mid → mimo-v2.5
        response = client.post("/v1/messages", headers={"x-api-key": "sk-payg-123"}, json=_build_base_body())
        assert response.status_code == 200
        assert captured["url"] == "https://mimo-payg.example/v1/messages"
        assert captured["model"] == "mimo-v2.5"

        # sk-kimi-* 路由到 Kimi codingplan，模型用 kimi-for-coding
        response = client.post("/v1/messages", headers={"x-api-key": "sk-kimi-123"}, json=_build_base_body())
        assert response.status_code == 200
        assert captured["url"] == "https://kimi-coding.example/v1/messages"
        assert captured["model"] == "kimi-for-coding"

    def test_env_key_locks_provider(self, monkeypatch):
        """auto 模式下设置某 provider 的 env key 后，所有流量应被锁定到该 provider。"""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        # 设置 DeepSeek env key，即使 incoming key 是 tp-* 也应路由到 DeepSeek
        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "auto",
            "DEEPSEEK_API_KEY": "sk-env-ds-key",
            "DEEPSEEK_BASE_URL": "https://ds.example/anthropic",
            "KIMI_CODING_BASE_URL": "https://kimi-coding.example",
            "KIMI_PAYG_BASE_URL": "https://kimi-payg.example",
            "MIMO_PAYG_BASE_URL": "https://mimo-payg.example",
            "MIMO_TP_BASE_URL_CN": "https://mimo-tp.example",
            "MODEL_PRIMARY": "mimo-v2.5",
            "MODEL_MID": "mimo-v2.5",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        # 即使 key 是 tp-*，也应被锁定到 DeepSeek
        response = client.post("/v1/messages", headers={"x-api-key": "tp-test-key"}, json=_build_base_body())
        assert response.status_code == 200
        assert captured["url"] == "https://ds.example/anthropic/v1/messages"


class TestKimiKeyClassification:
    """测试 Kimi key 前缀分类逻辑的 bug 修复。"""

    def test_sk_kimi_classified_as_codingplan(self, monkeypatch):
        """sk-kimi-* 应该路由到 codingplan，而不是 PAYG。"""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            body = json.loads(request.content)
            captured["model"] = body["model"]
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "kimi",
            "KIMI_API_KEY": "",
            "KIMI_CODING_BASE_URL": "https://coding.example",
            "KIMI_PAYG_BASE_URL": "https://payg.example",
            "CODINGPLAN_MODEL": "kimi-for-coding",
            "MODEL_PRIMARY": "kimi-k2.6",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", headers={"x-api-key": "sk-kimi-worker-123"}, json=_build_base_body())
        assert response.status_code == 200
        assert captured["url"] == "https://coding.example/v1/messages"
        assert captured["model"] == "kimi-for-coding"

    def test_sk_generic_classified_as_payg(self, monkeypatch):
        """普通 sk-* 应该路由到 PAYG。"""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "kimi",
            "KIMI_API_KEY": "",
            "KIMI_CODING_BASE_URL": "https://coding.example",
            "KIMI_PAYG_BASE_URL": "https://payg.example",
            "MODEL_PRIMARY": "kimi-k2.6",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", headers={"x-api-key": "sk-payg-456"}, json=_build_base_body())
        assert response.status_code == 200
        assert captured["url"] == "https://payg.example/v1/messages"

    def test_non_sk_key_returns_401(self, monkeypatch):
        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "kimi",
            "KIMI_API_KEY": "",
        })
        client = TestClient(module.app)
        response = client.post("/v1/messages", headers={"x-api-key": "tp-not-kimi"}, json=_build_base_body())
        assert response.status_code == 401


class TestSharedEndpoints:
    def test_healthz(self, monkeypatch):
        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test",
        })
        client = TestClient(module.app)
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["provider"] == "deepseek"

    def test_models(self, monkeypatch):
        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test",
            "MODEL_PRIMARY": "deepseek-v4-pro",
            "MODEL_FAST": "deepseek-v4-flash",
        })
        client = TestClient(module.app)
        response = client.get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        ids = [item["id"] for item in data["data"]]

        # 严格客户端通常优先读取前几项，前 3 项必须稳定暴露三档 versioned Claude 模型
        assert ids[:2] == [
            "claude-opus-4-5",
            "claude-sonnet-4-5",
        ]

        # 同时保留简写 alias（仅 Opus/Sonnet 两档）
        assert {"opus", "sonnet"}.issubset(set(ids))
        assert "haiku" not in set(ids)
        assert len(ids) == len(set(ids))

        # 顶层兼容字段：便于部分 OpenAI 风格/严格解析客户端发现模型列表
        assert data["object"] == "list"
        assert data["first_id"] == "claude-opus-4-5"
        assert data["last_id"] == ids[-1]
        assert data["has_more"] is False

        # 单模型兼容字段
        first = data["data"][0]
        assert first["type"] == "model"
        assert first["object"] == "model"
        assert isinstance(first["name"], str) and first["name"]
        assert isinstance(first["created"], int)
        assert first["context_window"] == first["max_input_tokens"]
        assert first["max_output_tokens"] == first["max_tokens"]

    def test_models_dedup_when_alias_collide(self, monkeypatch):
        """当 alias 与 versioned alias 相同，应去重但保持两档可发现。"""
        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test",
            "ALIAS_OPUS": "claude-opus-4-5",
            "ALIAS_SONNET": "claude-sonnet-4-5",
        })
        client = TestClient(module.app)
        response = client.get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        ids = [item["id"] for item in data["data"]]
        assert ids == [
            "claude-opus-4-5",
            "claude-sonnet-4-5",
        ]

    def test_upstream_error_passthrough(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": {"type": "rate_limit", "message": "too fast"}})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/anthropic",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)
        response = client.post("/v1/messages", json=_build_base_body())
        assert response.status_code == 429

    def test_stream_tool_json_merge(self, monkeypatch):
        """测试流式模式下 tool_use input_json_delta 的碎片合并。"""

        class StaticAsyncByteStream(httpx.AsyncByteStream):
            def __init__(self, chunks):
                self._chunks = chunks

            async def __aiter__(self):
                for item in self._chunks:
                    yield item

            async def aclose(self):
                return None

        async def handler(request: httpx.Request) -> httpx.Response:
            chunks = [
                b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"t1","name":"search","input":{}}}\n\n',
                b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"q\\":\\"Shang"}}\n\n',
                b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"hai\\"}"}}\n\n',
                b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
                b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
                b'data: [DONE]\n\n',
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=StaticAsyncByteStream(chunks),
            )

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/anthropic",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)
        response = client.post("/v1/messages", json=_build_base_body(stream=True))

        assert response.status_code == 200
        merged_found = False
        for line in response.text.splitlines():
            if not line.startswith("data: "):
                continue
            raw = line[len("data: "):]
            if raw == "[DONE]":
                continue
            event = json.loads(raw)
            if (
                event.get("type") == "content_block_delta"
                and event.get("index") == 0
                and isinstance(event.get("delta"), dict)
                and event["delta"].get("type") == "input_json_delta"
            ):
                partial = event["delta"].get("partial_json")
                if isinstance(partial, str) and json.loads(partial) == {"q": "Shanghai"}:
                    merged_found = True
                    break
        assert merged_found

    def test_invalid_content_length_returns_400(self, monkeypatch):
        """Content-Length 非数字应返回 400。"""
        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/anthropic",
        })
        client = TestClient(module.app)
        response = client.post(
            "/v1/messages",
            content=json.dumps(_build_base_body()).encode(),
            headers={"content-type": "application/json", "content-length": "abc"},
        )
        assert response.status_code == 400

    def test_oversized_body_returns_413(self, monkeypatch):
        """超过 MAX_REQUEST_BODY_BYTES 的请求体应返回 413。"""
        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/anthropic",
        })
        client = TestClient(module.app)
        body = _build_base_body()
        body["messages"][0]["content"] = "x" * (5 * 1024 * 1024)  # 5MB > 4MB limit
        response = client.post("/v1/messages", json=body)
        assert response.status_code == 413

    def test_web_search_tool_disabled_drops_server_tool_blocks(self, monkeypatch):
        """开关关闭时，web_search server tool 相关块应被 sanitize 丢弃（保持现状）。"""
        captured: Dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured["body"] = body
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/anthropic",
            "ENABLE_WEB_SEARCH_TOOL": "false",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", json=_build_web_search_body())
        assert response.status_code == 200

        sent = captured["body"]
        # tools 不应包含 server-tool type
        assert all(tool.get("type") != "web_search_20250305" for tool in sent.get("tools", []))
        # server_tool_use / web_search_tool_result 被过滤（兼容消息被删除后的索引变化）
        block_types = []
        for msg in sent.get("messages", []):
            content = msg.get("content") if isinstance(msg, dict) else None
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        block_types.append(block.get("type"))
        assert "server_tool_use" not in block_types
        assert "web_search_tool_result" not in block_types

    def test_web_search_tool_enabled_passthrough(self, monkeypatch):
        """开关开启时，web_search server tool 与对应结果块应透传到上游。"""
        captured: Dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured["body"] = body
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/anthropic",
            "ENABLE_WEB_SEARCH_TOOL": "true",
            "ENABLE_AUTO_WEB_SEARCH_EXECUTION": "false",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", json=_build_web_search_body())
        assert response.status_code == 200

        sent = captured["body"]
        # tools 中保留 web_search server tool 定义
        assert any(tool.get("type") == "web_search_20250305" for tool in sent.get("tools", []))
        # assistant content 中保留 server_tool_use + web_search_tool_result
        first_msg_blocks = sent["messages"][0]["content"]
        block_types = [block.get("type") for block in first_msg_blocks]
        assert "server_tool_use" in block_types
        assert "web_search_tool_result" in block_types

    def test_web_search_auto_execution_followup(self, monkeypatch):
        """开启自动执行时，第一轮 tool_use 应触发网关二轮补 tool_result。"""
        captured: Dict[str, Any] = {"calls": []}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured["calls"].append(body)
            if len(captured["calls"]) == 1:
                return httpx.Response(
                    200,
                    json={
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "model": "mimo-v2.5",
                        "stop_reason": "tool_use",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_123",
                                "name": "web_search",
                                "input": {"query": "北京天气 今天"},
                            }
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": "msg_2",
                    "type": "message",
                    "role": "assistant",
                    "model": "mimo-v2.5",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "final answer"}],
                },
            )

        async def fake_search(*args, **kwargs):
            return [
                {
                    "title": "Weather Example",
                    "url": "https://example.com/weather",
                    "snippet": "sunny",
                }
            ]

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "mimo",
            "MIMO_API_KEY": "sk-test",
            "MIMO_PAYG_BASE_URL": "https://api.xiaomimimo.com/anthropic",
            "ENABLE_WEB_SEARCH_TOOL": "true",
            "ENABLE_AUTO_WEB_SEARCH_EXECUTION": "true",
        })
        monkeypatch.setattr(module, "search_duckduckgo_html", fake_search)
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", json=_build_web_search_user_body())
        assert response.status_code == 200
        data = response.json()
        assert data.get("stop_reason") == "end_turn"

        assert len(captured["calls"]) == 2
        second = captured["calls"][1]
        messages = second.get("messages", [])
        assert len(messages) >= 3
        # 最后一条 user 消息应包含 tool_result 回填
        last = messages[-1]
        assert last.get("role") == "user"
        content = last.get("content")
        assert isinstance(content, list) and content
        block = content[0]
        assert block.get("type") == "tool_result"
        assert block.get("tool_use_id") == "toolu_123"
        assert "example.com/weather" in block.get("content", "")
        # 方案 C：补完第一轮后必须禁止继续 tool_use
        assert second.get("tool_choice") == {"type": "none"}
        assert "tools" not in second

    def test_web_search_auto_execution_disabled_keeps_tool_use(self, monkeypatch):
        """关闭自动执行时，应保持第一轮 tool_use 原样返回。"""
        captured: Dict[str, Any] = {"calls": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["calls"] += 1
            return httpx.Response(
                200,
                json={
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "mimo-v2.5",
                    "stop_reason": "tool_use",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_123",
                            "name": "web_search",
                            "input": {"query": "北京天气 今天"},
                        }
                    ],
                },
            )

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "mimo",
            "MIMO_API_KEY": "sk-test",
            "MIMO_PAYG_BASE_URL": "https://api.xiaomimimo.com/anthropic",
            "ENABLE_WEB_SEARCH_TOOL": "true",
            "ENABLE_AUTO_WEB_SEARCH_EXECUTION": "false",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", json=_build_web_search_user_body())
        assert response.status_code == 200
        data = response.json()
        assert data.get("stop_reason") == "tool_use"
        assert captured["calls"] == 1

    def test_web_search_auto_execution_supports_xml_tool_call_fallback(self, monkeypatch):
        """上游未返回 tool_use block，仅返回 <tool_call> 文本时也应触发二轮。"""
        captured: Dict[str, Any] = {"calls": []}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured["calls"].append(body)
            if len(captured["calls"]) == 1:
                return httpx.Response(
                    200,
                    json={
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "model": "mimo-v2.5",
                        "stop_reason": "tool_use",
                        "content": [
                            {
                                "type": "text",
                                "text": "<tool_call>\\n<function=web_search>\\n<parameter=query>北京天气 今天</parameter>\\n</function>\\n</tool_call>",
                            }
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": "msg_2",
                    "type": "message",
                    "role": "assistant",
                    "model": "mimo-v2.5",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "ok"}],
                },
            )

        async def fake_search(*args, **kwargs):
            return [{"title": "t", "url": "https://example.com/x", "snippet": "s"}]

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "mimo",
            "MIMO_API_KEY": "sk-test",
            "MIMO_PAYG_BASE_URL": "https://api.xiaomimimo.com/anthropic",
            "ENABLE_WEB_SEARCH_TOOL": "true",
            "ENABLE_AUTO_WEB_SEARCH_EXECUTION": "true",
        })
        monkeypatch.setattr(module, "search_duckduckgo_html", fake_search)
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", json=_build_web_search_user_body())
        assert response.status_code == 200
        assert response.json().get("stop_reason") == "end_turn"
        assert len(captured["calls"]) == 2

        second = captured["calls"][1]
        last = second["messages"][-1]
        block = last["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"].startswith("fallback_web_search_")
        assistant_msg = second["messages"][-2]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"][0]["type"] == "tool_use"
        assert assistant_msg["content"][0]["id"].startswith("fallback_web_search_")
        assert second.get("tool_choice") == {"type": "none"}
        assert "tools" not in second

    def test_web_search_auto_execution_multi_round_converges(self, monkeypatch):
        """第一轮和第二轮都返回 tool_use 时，应在多轮上限内继续推进到最终回答。"""
        captured: Dict[str, Any] = {"calls": []}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured["calls"].append(body)
            n = len(captured["calls"])
            if n in (1, 2):
                return httpx.Response(
                    200,
                    json={
                        "id": f"msg_{n}",
                        "type": "message",
                        "role": "assistant",
                        "model": "mimo-v2.5",
                        "stop_reason": "tool_use",
                        "content": [
                            {
                                "type": "text",
                                "text": "<tool_call>\\n<function=web_search>\\n<parameter=query></parameter>\\n</function>\\n</tool_call>",
                            }
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": "msg_3",
                    "type": "message",
                    "role": "assistant",
                    "model": "mimo-v2.5",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "done"}],
                },
            )

        async def fake_search(*args, **kwargs):
            return [{"title": "t", "url": "https://example.com/x", "snippet": "s"}]

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "mimo",
            "MIMO_API_KEY": "sk-test",
            "MIMO_PAYG_BASE_URL": "https://api.xiaomimimo.com/anthropic",
            "ENABLE_WEB_SEARCH_TOOL": "true",
            "ENABLE_AUTO_WEB_SEARCH_EXECUTION": "true",
            "AUTO_WEB_SEARCH_MAX_ROUNDS": "3",
        })
        monkeypatch.setattr(module, "search_duckduckgo_html", fake_search)
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", json=_build_web_search_user_body())
        assert response.status_code == 200
        assert response.json().get("stop_reason") == "end_turn"
        assert len(captured["calls"]) == 3

    def test_web_search_auto_execution_supports_end_turn_xml_tool_call(self, monkeypatch):
        """上游返回 end_turn + <tool_call> 文本时，也应触发本地搜索回路。"""
        captured: Dict[str, Any] = {"calls": []}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured["calls"].append(body)

            if len(captured["calls"]) == 1:
                return httpx.Response(
                    200,
                    json={
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "model": "mimo-v2.5",
                        "stop_reason": "end_turn",
                        "content": [
                            {
                                "type": "text",
                                "text": "<tool_call>\n<function=web_search>\n<parameter=query>北京天气 今天</parameter>\n</function>\n</tool_call>",
                            }
                        ],
                    },
                )

            return httpx.Response(
                200,
                json={
                    "id": "msg_2",
                    "type": "message",
                    "role": "assistant",
                    "model": "mimo-v2.5",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "final with https://example.com/weather"}],
                },
            )

        async def fake_search(*args, **kwargs):
            return [{"title": "Weather", "url": "https://example.com/weather", "snippet": "sunny"}]

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "mimo",
            "MIMO_API_KEY": "sk-test",
            "MIMO_PAYG_BASE_URL": "https://api.xiaomimimo.com/anthropic",
            "ENABLE_WEB_SEARCH_TOOL": "true",
            "ENABLE_AUTO_WEB_SEARCH_EXECUTION": "true",
        })
        monkeypatch.setattr(module, "search_duckduckgo_html", fake_search)
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", json=_build_web_search_user_body())
        assert response.status_code == 200
        assert response.json().get("stop_reason") == "end_turn"
        assert len(captured["calls"]) == 2

        second = captured["calls"][1]
        assistant_msg = second["messages"][-2]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"][0]["type"] == "tool_use"
        assert assistant_msg["content"][0]["id"].startswith("fallback_web_search_")

        user_msg = second["messages"][-1]
        assert user_msg["content"][0]["type"] == "tool_result"
        assert "example.com/weather" in user_msg["content"][0]["content"]

    def test_web_search_auto_execution_retries_when_followup_still_xml_tool_call(self, monkeypatch):
        """二轮仍返回 end_turn + XML tool_call 时，应继续下一轮而不是直接返回给客户端。"""
        captured: Dict[str, Any] = {"calls": []}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured["calls"].append(body)
            n = len(captured["calls"])
            if n == 1:
                return httpx.Response(
                    200,
                    json={
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "model": "mimo-v2.5",
                        "stop_reason": "end_turn",
                        "content": [
                            {
                                "type": "text",
                                "text": "<tool_call>\n<function=web_search>\n<parameter=query>北京天气 今天</parameter>\n</function>\n</tool_call>",
                            }
                        ],
                    },
                )
            if n == 2:
                return httpx.Response(
                    200,
                    json={
                        "id": "msg_2",
                        "type": "message",
                        "role": "assistant",
                        "model": "mimo-v2.5",
                        "stop_reason": "end_turn",
                        "content": [
                            {
                                "type": "text",
                                "text": "<tool_call>\n<function=web_search>\n<parameter=query>weather.com Beijing</parameter>\n</function>\n</tool_call>",
                            }
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": "msg_3",
                    "type": "message",
                    "role": "assistant",
                    "model": "mimo-v2.5",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "final with https://example.com/weather"}],
                },
            )

        async def fake_search(*args, **kwargs):
            return [{"title": "Weather", "url": "https://example.com/weather", "snippet": "sunny"}]

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "mimo",
            "MIMO_API_KEY": "sk-test",
            "MIMO_PAYG_BASE_URL": "https://api.xiaomimimo.com/anthropic",
            "ENABLE_WEB_SEARCH_TOOL": "true",
            "ENABLE_AUTO_WEB_SEARCH_EXECUTION": "true",
            "AUTO_WEB_SEARCH_MAX_ROUNDS": "3",
        })
        monkeypatch.setattr(module, "search_duckduckgo_html", fake_search)
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", json=_build_web_search_user_body())
        assert response.status_code == 200
        assert response.json().get("stop_reason") == "end_turn"
        assert len(captured["calls"]) == 3

    def test_web_search_auto_execution_fallback_answer_when_xml_persists(self, monkeypatch):
        """达到轮次上限后仍是 XML tool_call，应返回网关本地聚合答案而非 <tool_call>。"""
        captured: Dict[str, Any] = {"calls": []}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured["calls"].append(body)
            return httpx.Response(
                200,
                json={
                    "id": f"msg_{len(captured['calls'])}",
                    "type": "message",
                    "role": "assistant",
                    "model": "mimo-v2.5",
                    "stop_reason": "end_turn",
                    "content": [
                        {
                            "type": "text",
                            "text": "<tool_call>\n<function=web_search>\n<parameter=query>weather Beijing</parameter>\n</function>\n</tool_call>",
                        }
                    ],
                },
            )

        async def fake_search(*args, **kwargs):
            return [{"title": "Weather", "url": "https://example.com/weather", "snippet": "sunny"}]

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "mimo",
            "MIMO_API_KEY": "sk-test",
            "MIMO_PAYG_BASE_URL": "https://api.xiaomimimo.com/anthropic",
            "ENABLE_WEB_SEARCH_TOOL": "true",
            "ENABLE_AUTO_WEB_SEARCH_EXECUTION": "true",
            "AUTO_WEB_SEARCH_MAX_ROUNDS": "2",
        })
        monkeypatch.setattr(module, "search_duckduckgo_html", fake_search)
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", json=_build_web_search_user_body())
        assert response.status_code == 200
        data = response.json()
        assert data.get("stop_reason") == "end_turn"
        text_blocks = [b.get("text", "") for b in data.get("content", []) if isinstance(b, dict) and b.get("type") == "text"]
        assert text_blocks
        assert "<tool_call>" not in text_blocks[0]
        assert "https://example.com/weather" in text_blocks[0]

    def test_web_search_auto_execution_rewrites_server_tool_to_client_tool(self, monkeypatch):
        """方案 A：自动执行开启时，入站 web_search_* 必须改写为 client tool。"""
        captured: Dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured["body"] = body
            return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

        module = _reload_main(monkeypatch, {
            "ACTIVE_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-test",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/anthropic",
            "ENABLE_WEB_SEARCH_TOOL": "true",
            "ENABLE_AUTO_WEB_SEARCH_EXECUTION": "true",
        })
        _wire_async_client(monkeypatch, module, handler)
        client = TestClient(module.app)

        response = client.post("/v1/messages", json=_build_web_search_user_body())
        assert response.status_code == 200
        sent_tools = captured["body"].get("tools", [])
        assert sent_tools
        first_tool = sent_tools[0]
        assert first_tool.get("name") == "web_search"
        assert "type" not in first_tool
        assert first_tool.get("input_schema", {}).get("type") == "object"
