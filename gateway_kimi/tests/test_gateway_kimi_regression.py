import json
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

import app.main as gateway_main


def _base_payload() -> Dict[str, Any]:
    return {
        "model": "claude-sonnet-4-5",
        "max_tokens": 64,
        "stream": False,
        "messages": [{"role": "user", "content": "ping"}],
    }


class DummyUpstreamResponse:
    def __init__(self, status_code: int, json_data: Dict[str, Any] | None = None, text: str | None = None) -> None:
        self.status_code = status_code
        self._json_data = json_data
        if text is not None:
            self.text = text
        elif json_data is not None:
            self.text = json.dumps(json_data, ensure_ascii=False)
        else:
            self.text = ""

    def json(self) -> Dict[str, Any]:
        if self._json_data is None:
            raise ValueError("No JSON payload available")
        return self._json_data


class DummyUpstreamStreamResponse:
    def __init__(self, status_code: int, lines: List[str], error_json: Dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._lines = lines
        self._error_json = error_json
        self.closed = False

    async def aread(self) -> bytes:
        if self._error_json is None:
            return b""
        return json.dumps(self._error_json, ensure_ascii=False).encode("utf-8")

    async def aclose(self) -> None:
        self.closed = True

    async def aiter_lines(self):
        for line in self._lines:
            yield line


@pytest.fixture
def configured_gateway(monkeypatch):
    monkeypatch.setattr(gateway_main, "KIMI_API_KEY", "")
    monkeypatch.setattr(gateway_main, "UPSTREAM_API_KEY", "")
    monkeypatch.setattr(gateway_main, "CODINGPLAN_BASE_URL", "https://coding.example")
    monkeypatch.setattr(gateway_main, "PAYG_BASE_URL", "https://payg.example")
    monkeypatch.setattr(gateway_main, "PAYG_MODEL_PRIMARY", "kimi-payg-primary")
    monkeypatch.setattr(gateway_main, "PAYG_MODEL_FAST", "kimi-payg-fast")
    monkeypatch.setattr(gateway_main, "CODINGPLAN_MODEL", "kimi-for-coding")
    monkeypatch.setattr(gateway_main, "ALIAS_SONNET", "claude-sonnet-4-5")
    monkeypatch.setattr(gateway_main, "ALIAS_SONNET_VERSIONED", "claude-sonnet-4-5-20250929")
    monkeypatch.setattr(gateway_main, "ALIAS_OPUS", "claude-opus-4-5")
    monkeypatch.setattr(gateway_main, "ALIAS_OPUS_VERSIONED", "claude-opus-4-5-20251101")
    monkeypatch.setattr(gateway_main, "ALIAS_HAIKU", "claude-haiku-4-5")
    monkeypatch.setattr(gateway_main, "ALIAS_HAIKU_VERSIONED", "claude-haiku-4-5-20251001")
    return gateway_main


@pytest.fixture
def upstream_stub(monkeypatch, configured_gateway):
    calls: List[Dict[str, Any]] = []
    state: Dict[str, Any] = {
        "post_response": DummyUpstreamResponse(200, {"type": "message", "id": "msg-ok", "content": []}),
        "post_exception": None,
        "stream_response": DummyUpstreamStreamResponse(
            200,
            [
                "event: message_stop",
                'data: {"type":"message_stop"}',
                "",
                "data: [DONE]",
                "",
            ],
        ),
    }

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aclose(self):
            calls.append({"kind": "aclose"})

        async def post(self, url, headers=None, json=None):
            calls.append({"kind": "post", "url": str(url), "headers": headers, "json": json})
            if state["post_exception"] is not None:
                raise state["post_exception"]
            return state["post_response"]

        def build_request(self, method, url, headers=None, json=None):
            request = {"method": method, "url": str(url), "headers": headers, "json": json}
            calls.append({"kind": "build_request", **request})
            return request

        async def send(self, request, stream=False):
            calls.append({"kind": "send", "request": request, "stream": stream})
            return state["stream_response"]

    monkeypatch.setattr(configured_gateway.httpx, "AsyncClient", MockAsyncClient)
    return calls, state


@pytest.fixture
def client(configured_gateway):
    with TestClient(configured_gateway.app) as test_client:
        yield test_client


def _post_record(calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    return next(item for item in calls if item["kind"] == "post")


def _parse_sse_frames(raw_text: str) -> List[Dict[str, Any]]:
    frames: List[Dict[str, Any]] = []
    for chunk in raw_text.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        event_name = None
        data_lines: List[str] = []
        for line in chunk.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
        frames.append({"event": event_name, "data": "\n".join(data_lines)})
    return frames


def test_sk_kimi_routes_to_coding_and_forces_model(client, upstream_stub):
    calls, _ = upstream_stub
    payload = _base_payload()
    payload["model"] = "claude-haiku-4-5"

    response = client.post("/v1/messages", headers={"x-api-key": "sk-kimi-123"}, json=payload)

    assert response.status_code == 200
    call = _post_record(calls)
    assert call["url"] == "https://coding.example/v1/messages"
    assert call["headers"]["x-api-key"] == "sk-kimi-123"
    assert call["json"]["model"] == "kimi-for-coding"


@pytest.mark.parametrize(
    ("input_model", "expected_model"),
    [
        ("claude-sonnet-4-5", "kimi-payg-primary"),
        ("claude-haiku-4-5", "kimi-payg-fast"),
        ("unknown-model", "kimi-payg-primary"),
        ("", "kimi-payg-primary"),
    ],
)
def test_regular_sk_routes_to_payg_with_alias_default_mapping(client, upstream_stub, input_model, expected_model):
    calls, _ = upstream_stub
    payload = _base_payload()
    if input_model:
        payload["model"] = input_model
    else:
        payload.pop("model", None)

    response = client.post("/v1/messages", headers={"x-api-key": "sk-payg-456"}, json=payload)

    assert response.status_code == 200
    call = _post_record(calls)
    assert call["url"] == "https://payg.example/v1/messages"
    assert call["json"]["model"] == expected_model


def test_missing_key_returns_401(client, upstream_stub):
    calls, _ = upstream_stub
    response = client.post("/v1/messages", json=_base_payload())

    assert response.status_code == 401
    assert "No API key available" in response.json()["detail"]
    assert not any(item["kind"] in {"post", "send"} for item in calls)


def test_invalid_key_returns_401(client, upstream_stub):
    calls, _ = upstream_stub
    response = client.post("/v1/messages", headers={"x-api-key": "not-a-sk-key"}, json=_base_payload())

    assert response.status_code == 401
    assert "Invalid API key format" in response.json()["detail"]
    assert not any(item["kind"] in {"post", "send"} for item in calls)


@pytest.mark.parametrize("status_code", [401, 429])
def test_upstream_error_passthrough(client, upstream_stub, status_code):
    calls, state = upstream_stub
    state["post_response"] = DummyUpstreamResponse(
        status_code,
        {"error": {"type": "upstream_error", "message": f"status={status_code}"}},
    )

    response = client.post("/v1/messages", headers={"x-api-key": "sk-payg-456"}, json=_base_payload())

    assert response.status_code == status_code
    assert response.json()["error"]["type"] == "upstream_error"
    assert _post_record(calls)["url"] == "https://payg.example/v1/messages"


def test_invalid_thinking_block_is_sanitized_to_text(client, upstream_stub):
    calls, _ = upstream_stub
    payload = _base_payload()
    payload["messages"] = [
        {
            "role": "user",
            "content": [
                {"type": "thinking", "thinking": "", "text": "fallback-thinking-as-text"},
            ],
        }
    ]

    response = client.post("/v1/messages", headers={"x-api-key": "sk-payg-456"}, json=payload)

    assert response.status_code == 200
    sanitized_content = _post_record(calls)["json"]["messages"][0]["content"]
    assert sanitized_content == [{"type": "text", "text": "fallback-thinking-as-text"}]


def test_image_block_is_preserved(client, upstream_stub):
    calls, _ = upstream_stub
    payload = _base_payload()
    payload["messages"] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"},
                },
                {"type": "text", "text": "describe"},
            ],
        }
    ]

    response = client.post("/v1/messages", headers={"x-api-key": "sk-payg-456"}, json=payload)

    assert response.status_code == 200
    content_blocks = _post_record(calls)["json"]["messages"][0]["content"]
    assert content_blocks[0]["type"] == "image_url"
    assert content_blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_stream_sse_shim_normalizes_input_json_delta(client, upstream_stub):
    calls, state = upstream_stub
    state["stream_response"] = DummyUpstreamStreamResponse(
        200,
        [
            "event: content_block_delta",
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"a\\":"}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"1}"}}',
            "",
            "event: content_block_stop",
            'data: {"type":"content_block_stop","index":0}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
            "data: [DONE]",
            "",
        ],
    )
    payload = _base_payload()
    payload["stream"] = True

    response = client.post("/v1/messages", headers={"x-api-key": "sk-payg-456"}, json=payload)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _parse_sse_frames(response.text)

    synthetic_starts = [
        json.loads(item["data"])
        for item in frames
        if item["event"] == "content_block_start"
    ]
    assert synthetic_starts
    assert synthetic_starts[0]["content_block"]["type"] == "tool_use"

    normalized_deltas = [
        json.loads(item["data"])
        for item in frames
        if item["event"] == "content_block_delta"
    ]
    assert any(
        item.get("delta", {}).get("type") == "input_json_delta"
        and item.get("delta", {}).get("partial_json") == '{"a":1}'
        for item in normalized_deltas
    )

    build_call = next(item for item in calls if item["kind"] == "build_request")
    assert build_call["url"] == "https://payg.example/v1/messages"
