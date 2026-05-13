import json
from typing import Any, Dict

import pytest

from claude_gateway.sanitize import (
    MAX_IMAGE_BASE64_LEN,
    MAX_TOOL_INPUT_DEPTH,
    get_supported_content_types,
    looks_like_connection_probe,
    normalize_messages,
    normalize_system,
    sanitize_content_block,
    sanitize_output_config,
    sanitize_request_body,
    sanitize_thinking,
    sanitize_tools,
)


class TestNormalizeSystem:
    def test_string_passthrough(self):
        assert normalize_system("hello") == "hello"

    def test_list_of_strings(self):
        result = normalize_system(["a", "b"])
        assert result == [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]

    def test_list_of_text_blocks(self):
        result = normalize_system([{"type": "text", "text": "a"}])
        assert result == [{"type": "text", "text": "a"}]

    def test_empty_strings_filtered(self):
        result = normalize_system(["a", "", "  ", "b"])
        assert result == [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]

    def test_dict_text_block(self):
        result = normalize_system({"type": "text", "text": "hello"})
        assert result == [{"type": "text", "text": "hello"}]

    def test_fallback_to_str(self):
        assert normalize_system(42) == "42"


class TestSanitizeContentBlock:
    def test_text_string(self):
        dropped: Dict[str, int] = {}
        result = sanitize_content_block("hello", dropped, {"text"})
        assert result == {"type": "text", "text": "hello"}

    def test_empty_string_dropped(self):
        dropped: Dict[str, int] = {}
        result = sanitize_content_block("  ", dropped, {"text"})
        assert result is None
        assert dropped["empty_string_block"] == 1

    def test_text_block(self):
        dropped: Dict[str, int] = {}
        result = sanitize_content_block({"type": "text", "text": "hi"}, dropped, {"text"})
        assert result == {"type": "text", "text": "hi"}

    def test_thinking_block(self):
        dropped: Dict[str, int] = {}
        result = sanitize_content_block(
            {"type": "thinking", "thinking": "reasoning", "signature": "sig1"},
            dropped,
            {"text", "thinking"},
        )
        assert result == {"type": "thinking", "thinking": "reasoning", "signature": "sig1"}

    def test_invalid_thinking_downgraded_to_text(self):
        dropped: Dict[str, int] = {}
        result = sanitize_content_block(
            {"type": "thinking", "thinking": "", "text": "fallback"},
            dropped,
            {"text", "thinking"},
        )
        assert result == {"type": "text", "text": "fallback"}
        assert dropped["invalid_thinking_block_downgraded"] == 1

    def test_unsupported_block_type(self):
        dropped: Dict[str, int] = {}
        result = sanitize_content_block({"type": "document"}, dropped, {"text"})
        assert result is None
        assert dropped["unsupported_block:document"] == 1

    def test_image_block_without_support(self):
        dropped: Dict[str, int] = {}
        result = sanitize_content_block(
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}},
            dropped,
            {"text"},
        )
        assert result is None
        assert dropped["unknown_block:image"] == 1

    def test_image_block_with_support(self):
        dropped: Dict[str, int] = {}
        result = sanitize_content_block(
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}},
            dropped,
            {"text", "image"},
        )
        assert result is not None
        assert result["type"] == "image"

    def test_image_base64_too_large_dropped(self):
        dropped: Dict[str, int] = {}
        result = sanitize_content_block(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "a" * (MAX_IMAGE_BASE64_LEN + 1),
                },
            },
            dropped,
            {"text", "image"},
        )
        assert result is None
        assert dropped["oversized_image_base64"] == 1

    def test_tool_use_block(self):
        dropped: Dict[str, int] = {}
        result = sanitize_content_block(
            {"type": "tool_use", "id": "t1", "name": "search", "input": {"q": "test"}},
            dropped,
            {"text", "tool_use"},
        )
        assert result == {"type": "tool_use", "id": "t1", "name": "search", "input": {"q": "test"}}

    def test_tool_use_input_too_deep_dropped(self):
        deep_input: Dict[str, Any] = {}
        for _ in range(MAX_TOOL_INPUT_DEPTH + 1):
            deep_input = {"k": deep_input}

        dropped: Dict[str, int] = {}
        result = sanitize_content_block(
            {"type": "tool_use", "id": "t1", "name": "search", "input": deep_input},
            dropped,
            {"text", "tool_use"},
        )
        assert result is None
        assert dropped["oversized_tool_input_depth"] == 1


class TestNormalizeMessages:
    def test_valid_messages(self):
        dropped: Dict[str, int] = {}
        msgs = [{"role": "user", "content": "hello"}]
        result = normalize_messages(msgs, dropped, {"text"})
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_invalid_role_filtered(self):
        dropped: Dict[str, int] = {}
        msgs = [{"role": "system", "content": "hi"}]
        result = normalize_messages(msgs, dropped, {"text"})
        assert len(result) == 0
        assert dropped["invalid_role:system"] == 1

    def test_blank_content_filtered(self):
        dropped: Dict[str, int] = {}
        msgs = [{"role": "user", "content": "  "}]
        result = normalize_messages(msgs, dropped, {"text"})
        assert len(result) == 0

    def test_block_content_sanitized(self):
        dropped: Dict[str, int] = {}
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        result = normalize_messages(msgs, dropped, {"text"})
        assert len(result) == 1
        assert result[0]["content"] == [{"type": "text", "text": "hi"}]


class TestConnectionProbe:
    def test_typical_probe(self):
        body = {"max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}
        assert looks_like_connection_probe(body) is True

    def test_normal_request(self):
        body = {"max_tokens": 100, "messages": [{"role": "user", "content": "hello world"}]}
        assert looks_like_connection_probe(body) is False

    def test_stream_not_probe(self):
        body = {"max_tokens": 1, "stream": True, "messages": [{"role": "user", "content": "hi"}]}
        assert looks_like_connection_probe(body) is False

    def test_with_system_not_probe(self):
        body = {"max_tokens": 1, "system": "test", "messages": [{"role": "user", "content": "hi"}]}
        assert looks_like_connection_probe(body) is False


class TestSanitizeRequestBody:
    def test_basic_request(self):
        raw = {"model": "claude-sonnet-4-5", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}]}
        body, dropped, removed = sanitize_request_body(raw)
        assert body["max_tokens"] == 100
        assert len(body["messages"]) == 1

    def test_max_tokens_defaulted(self):
        raw = {"messages": [{"role": "user", "content": "hi"}]}
        body, dropped, removed = sanitize_request_body(raw, default_max_tokens=2048)
        assert body["max_tokens"] == 2048
        assert dropped["max_tokens_defaulted"] == 1

    def test_probe_max_tokens_raised(self):
        raw = {"max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}
        body, dropped, removed = sanitize_request_body(raw, min_compat_max_tokens=16)
        assert body["max_tokens"] == 16

    def test_unknown_fields_removed(self):
        raw = {"messages": [{"role": "user", "content": "hi"}], "unknown_field": "value"}
        body, dropped, removed = sanitize_request_body(raw)
        assert "unknown_field" not in body
        assert "unknown_field" in removed
