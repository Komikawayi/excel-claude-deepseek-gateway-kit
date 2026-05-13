import httpx
import pytest

from claude_gateway import web_search


def test_search_duckduckgo_retries_once_on_connect_timeout_then_success(monkeypatch):
    attempts = {"count": 0}
    html_ok = """
    <html><body>
      <div class="result">
        <a class="result__a" href="https://example.com/a">Result A</a>
        <div class="result__snippet">Snippet A</div>
      </div>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ConnectTimeout("timeout", request=request)
        return httpx.Response(200, text=html_ok)

    class BoundAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(web_search.httpx, "AsyncClient", BoundAsyncClient)

    import asyncio

    results = asyncio.run(
        web_search.search_duckduckgo_html(
            "beijing weather",
            max_results=3,
            timeout_s=5.0,
            retries=1,
        )
    )
    assert attempts["count"] == 2
    assert len(results) == 1
    assert results[0]["url"] == "https://example.com/a"


def test_search_duckduckgo_raises_after_retry_exhausted(monkeypatch):
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        raise httpx.ConnectTimeout("timeout", request=request)

    class BoundAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(web_search.httpx, "AsyncClient", BoundAsyncClient)

    import asyncio

    with pytest.raises(httpx.ConnectTimeout):
        asyncio.run(
            web_search.search_duckduckgo_html(
                "beijing weather",
                max_results=3,
                timeout_s=5.0,
                retries=1,
            )
        )
    assert attempts["count"] == 2
