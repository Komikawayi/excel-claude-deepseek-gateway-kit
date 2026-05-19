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


def _load_main_with_env(monkeypatch: pytest.MonkeyPatch, env_overrides: Dict[str, str | None]):
    for key, value in env_overrides.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)

    if "app.main" in sys.modules:
        module = importlib.reload(sys.modules["app.main"])
    else:
        module = importlib.import_module("app.main")
    return module


def _wire_async_client(monkeypatch: pytest.MonkeyPatch, module, handler):
    class BoundAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", BoundAsyncClient)


def test_sk_key_routes_to_payg_base(monkeypatch: pytest.MonkeyPatch):
    module = _load_main_with_env(
        monkeypatch,
        {
            "MIMO_API_KEY": "",
            "MIMO_PAYG_BASE_URL": "https://payg.example/anthropic",
            "MIMO_TP_BASE_URL_CN": "https://tp-cn.example/anthropic",
            "MIMO_TP_BASE_URL_SGP": "https://tp-sgp.example/anthropic",
            "MIMO_TP_BASE_URL_AMS": "https://tp-ams.example/anthropic",
        },
    )

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["x_api_key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

    _wire_async_client(monkeypatch, module, handler)
    client = TestClient(module.app)
    response = client.post("/v1/messages", headers={"x-api-key": "sk-test-key"}, json=_build_base_body())

    assert response.status_code == 200
    assert captured["url"] == "https://payg.example/anthropic/v1/messages"
    assert captured["auth"] == "Bearer sk-test-key"
    assert captured["x_api_key"] == "sk-test-key"


def test_tp_key_routes_to_tp_base_with_default_cn(monkeypatch: pytest.MonkeyPatch):
    module = _load_main_with_env(
        monkeypatch,
        {
            "MIMO_API_KEY": "",
            "MIMO_TP_REGION": "cn",
            "MIMO_PAYG_BASE_URL": "https://payg.example/anthropic",
            "MIMO_TP_BASE_URL_CN": "https://tp-cn.example/anthropic",
            "MIMO_TP_BASE_URL_SGP": "https://tp-sgp.example/anthropic",
            "MIMO_TP_BASE_URL_AMS": "https://tp-ams.example/anthropic",
        },
    )

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

    _wire_async_client(monkeypatch, module, handler)
    client = TestClient(module.app)
    response = client.post("/v1/messages", headers={"x-api-key": "tp-test-key"}, json=_build_base_body())

    assert response.status_code == 200
    assert captured["url"] == "https://tp-cn.example/anthropic/v1/messages"


@pytest.mark.parametrize(
    ("region", "expected_base"),
    [
        ("sgp", "https://tp-sgp.example/anthropic/v1/messages"),
        ("ams", "https://tp-ams.example/anthropic/v1/messages"),
    ],
)
def test_tp_key_region_override_routes_correctly(
    monkeypatch: pytest.MonkeyPatch,
    region: str,
    expected_base: str,
):
    module = _load_main_with_env(
        monkeypatch,
        {
            "MIMO_API_KEY": "",
            "MIMO_TP_REGION": "cn",
            "MIMO_PAYG_BASE_URL": "https://payg.example/anthropic",
            "MIMO_TP_BASE_URL_CN": "https://tp-cn.example/anthropic",
            "MIMO_TP_BASE_URL_SGP": "https://tp-sgp.example/anthropic",
            "MIMO_TP_BASE_URL_AMS": "https://tp-ams.example/anthropic",
        },
    )

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"id": "ok", "type": "message", "content": []})

    _wire_async_client(monkeypatch, module, handler)
    client = TestClient(module.app)
    response = client.post(
        "/v1/messages",
        headers={"x-api-key": "tp-test-key", "x-mimo-tp-region": region},
        json=_build_base_body(),
    )

    assert response.status_code == 200
    assert captured["url"] == expected_base


def test_invalid_region_returns_400(monkeypatch: pytest.MonkeyPatch):
    module = _load_main_with_env(
        monkeypatch,
        {
            "MIMO_API_KEY": "",
            "MIMO_TP_REGION": "cn",
        },
    )
    client = TestClient(module.app)
    response = client.post(
        "/v1/messages",
        headers={"x-api-key": "sk-test-key", "x-mimo-tp-region": "moon"},
        json=_build_base_body(),
    )

    assert response.status_code == 400
    assert "x-mimo-tp-region" in response.json()["detail"]


@pytest.mark.parametrize(
    ("headers", "detail_hint"),
    [
        ({}, "No API key"),
        ({"x-api-key": "bad-prefix-key"}, "Invalid API key prefix"),
    ],
)
def test_missing_key_or_invalid_prefix_returns_401(
    monkeypatch: pytest.MonkeyPatch,
    headers: Dict[str, str],
    detail_hint: str,
):
    module = _load_main_with_env(monkeypatch, {"MIMO_API_KEY": None})
    client = TestClient(module.app)
    response = client.post("/v1/messages", headers=headers, json=_build_base_body())

    assert response.status_code == 401
    assert detail_hint in response.json()["detail"]


@pytest.mark.parametrize("upstream_status", [401, 402, 429])
def test_upstream_error_statuses_are_passthrough(
    monkeypatch: pytest.MonkeyPatch,
    upstream_status: int,
):
    module = _load_main_with_env(monkeypatch, {"MIMO_API_KEY": "sk-env-key"})

    upstream_payload = {"error": {"type": "upstream_error", "message": f"status={upstream_status}"}}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(upstream_status, json=upstream_payload)

    _wire_async_client(monkeypatch, module, handler)
    client = TestClient(module.app)
    response = client.post("/v1/messages", json=_build_base_body())

    assert response.status_code == upstream_status
    assert response.json() == upstream_payload


def test_stream_tool_input_json_fragments_are_merged_by_shim(monkeypatch: pytest.MonkeyPatch):
    module = _load_main_with_env(monkeypatch, {"MIMO_API_KEY": "tp-env-key"})

    class StaticAsyncByteStream(httpx.AsyncByteStream):
        def __init__(self, chunks):
            self._chunks = chunks

        async def __aiter__(self):
            for item in self._chunks:
                yield item

        async def aclose(self):
            return None

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["stream"] is True
        chunks = [
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"tool_1","name":"weather","input":{}}}\n\n'.encode(
                "utf-8"
            ),
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"city\\":\\"Shang"}}\n\n'.encode(
                "utf-8"
            ),
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"hai\\",\\"unit\\":\\"C\\"}"}}\n\n'.encode(
                "utf-8"
            ),
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'.encode("utf-8"),
            'event: message_stop\ndata: {"type":"message_stop"}\n\n'.encode("utf-8"),
            "data: [DONE]\n\n".encode("utf-8"),
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=StaticAsyncByteStream(chunks),
        )

    _wire_async_client(monkeypatch, module, handler)
    client = TestClient(module.app)
    response = client.post("/v1/messages", headers={"x-api-key": "tp-test-key"}, json=_build_base_body(stream=True))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    merged_found = False
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        raw = line[len("data: ") :]
        if raw == "[DONE]":
            continue
        event_payload = json.loads(raw)
        if (
            event_payload.get("type") == "content_block_delta"
            and event_payload.get("index") == 0
            and isinstance(event_payload.get("delta"), dict)
            and event_payload["delta"].get("type") == "input_json_delta"
        ):
            partial_json = event_payload["delta"].get("partial_json")
            if isinstance(partial_json, str) and json.loads(partial_json) == {"city": "Shanghai", "unit": "C"}:
                merged_found = True
                break

    assert merged_found
