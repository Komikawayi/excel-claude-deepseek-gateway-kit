import asyncio
import html
import re
from typing import Any, Dict, Iterable, List
from urllib.parse import parse_qs, unquote, urlparse

import httpx

_RESULT_ANCHOR_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    flags=re.IGNORECASE | re.DOTALL,
)
_RESULT_SNIPPET_RE = re.compile(
    r'class="result__snippet"[^>]*>(.*?)</(?:a|div)>',
    flags=re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
RETRYABLE_SEARCH_ERRORS = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)


def _strip_tags(raw: str) -> str:
    text = _TAG_RE.sub("", raw)
    return html.unescape(text).strip()


def _normalize_url(raw_href: str) -> str:
    value = html.unescape((raw_href or "").strip())
    if not value:
        return ""

    if value.startswith("//"):
        value = "https:" + value
    elif value.startswith("/l/?"):
        value = "https://duckduckgo.com" + value

    parsed = urlparse(value)
    if "duckduckgo.com" in parsed.netloc and parsed.path == "/l/":
        q = parse_qs(parsed.query)
        uddg = q.get("uddg", [""])[0]
        if uddg:
            return unquote(uddg).strip()
    return value


def _domain_allowed(url: str, allowed_domains: Iterable[str] | None) -> bool:
    if not allowed_domains:
        return True
    netloc = urlparse(url).netloc.lower()
    if not netloc:
        return False
    for item in allowed_domains:
        domain = (item or "").strip().lower()
        if not domain:
            continue
        if netloc == domain or netloc.endswith("." + domain):
            return True
    return False


async def _search_duckduckgo_html_once(
    query: str,
    *,
    max_results: int = 5,
    timeout_s: float = 20.0,
    allowed_domains: Iterable[str] | None = None,
) -> List[Dict[str, str]]:
    q = (query or "").strip()
    if not q:
        return []

    headers = {
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_s) as client:
        resp = await client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": q},
            headers=headers,
        )

    if resp.status_code >= 400:
        return []

    html_text = resp.text
    results: List[Dict[str, str]] = []
    for match in _RESULT_ANCHOR_RE.finditer(html_text):
        href = _normalize_url(match.group(1))
        if not href:
            continue
        if not _domain_allowed(href, allowed_domains):
            continue

        title = _strip_tags(match.group(2))
        if not title:
            continue

        tail = html_text[match.end(): match.end() + 2200]
        snippet_match = _RESULT_SNIPPET_RE.search(tail)
        snippet = _strip_tags(snippet_match.group(1)) if snippet_match else ""

        results.append(
            {
                "title": title,
                "url": href,
                "snippet": snippet,
            }
        )
        if len(results) >= max_results:
            break

    return results


async def search_duckduckgo_html(
    query: str,
    *,
    max_results: int = 5,
    timeout_s: float = 20.0,
    allowed_domains: Iterable[str] | None = None,
    retries: int = 1,
) -> List[Dict[str, str]]:
    last_exc: Exception | None = None
    total_attempts = max(1, retries + 1)
    for attempt in range(total_attempts):
        try:
            return await _search_duckduckgo_html_once(
                query,
                max_results=max_results,
                timeout_s=timeout_s,
                allowed_domains=allowed_domains,
            )
        except RETRYABLE_SEARCH_ERRORS as exc:
            last_exc = exc
            print(
                "[gateway web_search] retryable_error "
                f"attempt={attempt + 1}/{total_attempts} "
                f"type={type(exc).__name__}"
            )
            if attempt >= total_attempts - 1:
                raise
            await asyncio.sleep(0.35)

    if last_exc is not None:
        raise last_exc
    return []


def format_web_search_tool_result_text(query: str, results: List[Dict[str, Any]]) -> str:
    q = (query or "").strip()
    lines = [f"query: {q or '(empty)'}"]
    if not results:
        lines.append("no results found")
        return "\n".join(lines)

    for idx, item in enumerate(results, 1):
        title = str(item.get("title", "")).strip() or "(untitled)"
        url = str(item.get("url", "")).strip()
        snippet = str(item.get("snippet", "")).strip()
        lines.append(f"{idx}. {title}")
        if url:
            lines.append(f"url: {url}")
        if snippet:
            lines.append(f"snippet: {snippet}")
    return "\n".join(lines)
