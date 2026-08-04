"""
clients/http.py — the ONLY place that makes outbound HTTP requests.

Rule: providers never call requests.get() or httpx directly.
      They call app.get() / app.post() / safe_get() from here.

Benefits of this single-client design:
  - Add retries once → every provider gets them.
  - Add a proxy / WARP once → every provider gets it.
  - Add Cloudflare bypass once → every provider gets it.
  - Add rate limiting once → every provider gets it.
  - Add logging / metrics once → every provider gets them.

Internals:
  - Shared httpx.AsyncClient (HTTP/2, connection pool, keep-alive).
  - Per-domain Cloudflare cookie jar (cf_clearance replay).
  - FlareSolverr integration on CF challenge detection.
  - WARP SOCKS5 proxy auto-injected from per-request ContextVar.
  - orjson for fast JSON parsing.
  - BeautifulSoup4 (lxml) for HTML parsing.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
import orjson
from bs4 import BeautifulSoup

from app.config import get_settings

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)

_settings = get_settings()

# ── Shared client ──────────────────────────────────────────────────────────────
_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    http2=True,
                    follow_redirects=True,
                    timeout=httpx.Timeout(30.0),
                    limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
                    headers={"User-Agent": UA},
                    verify=True,
                )
    return _client


# ── Cloudflare cookie jar ──────────────────────────────────────────────────────
_cookie_jar: dict[str, dict[str, str]] = {}


def set_clearance(url: str, cookie: str, user_agent: str) -> None:
    host = _host_of(url)
    if host and cookie:
        _cookie_jar[host] = {"cookie": cookie, "userAgent": user_agent}


def _clearance_for(url: str) -> Optional[dict[str, str]]:
    return _cookie_jar.get(_host_of(url))


def _host_of(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def _looks_like_cloudflare(status: int, text: str) -> bool:
    if status in (403, 503):
        return True
    return any(k in text for k in (
        "Just a moment", "cf-browser-verification",
        "Checking your browser", "__cf_chl", "Enable JavaScript",
    ))


def _merge_clearance(url: str, headers: dict[str, str]) -> dict[str, str]:
    clz = _clearance_for(url)
    if not clz:
        return headers
    merged = dict(headers)
    merged["User-Agent"] = clz.get("userAgent") or merged.get("User-Agent", UA)
    existing = merged.get("Cookie", "")
    merged["Cookie"] = f"{existing}; {clz['cookie']}" if existing else clz["cookie"]
    return merged


# ── SpResponse ─────────────────────────────────────────────────────────────────

@dataclass
class SpResponse:
    status: int
    text: str
    url: str
    cookie_header: str = ""
    response_headers: dict[str, str] = field(default_factory=dict)

    @property
    def is_successful(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        try:
            return orjson.loads(self.text)
        except Exception:
            return None

    @property
    def document(self) -> BeautifulSoup:
        return BeautifulSoup(self.text, "lxml")


def _build_response(
    status: int,
    text: str,
    url: str,
    cookie_header: str = "",
    response_headers: Optional[dict[str, str]] = None,
) -> SpResponse:
    return SpResponse(
        status=status,
        text=text,
        url=url,
        cookie_header=cookie_header,
        response_headers=response_headers or {},
    )


async def _do_request(
    method: str,
    url: str,
    headers: dict[str, str],
    data: Any = None,
    params: Optional[dict] = None,
    timeout: float = 15.0,
    proxy: Optional[str] = None,
) -> SpResponse:
    if proxy:
        async with httpx.AsyncClient(
            http2=False,
            follow_redirects=True,
            timeout=httpx.Timeout(timeout),
            proxies={"all://": proxy},
            headers={"User-Agent": UA},
        ) as proxied:
            resp = await proxied.request(method, url, headers=headers, content=data, params=params)
    else:
        client = await get_client()
        resp = await client.request(
            method, url, headers=headers, content=data, params=params, timeout=timeout,
        )

    resp_headers = {k.lower(): v for k, v in resp.headers.items()}
    return _build_response(resp.status_code, resp.text, str(resp.url), "", resp_headers)


# ── Public HTTP helpers ────────────────────────────────────────────────────────

class _App:
    """
    The one object providers call to make HTTP requests.
    WARP proxy is auto-injected from context — no provider changes needed.
    """

    async def get(
        self,
        url: str,
        headers: Optional[dict[str, str]] = None,
        referer: Optional[str] = None,
        timeout: float = 15.0,
        params: Optional[dict] = None,
        proxy: Optional[str] = None,
    ) -> SpResponse:
        if proxy is None:
            from app.utils.warp import warp_proxy
            proxy = warp_proxy()

        h = {**{"User-Agent": UA}, **(headers or {})}
        if referer:
            h["Referer"] = referer
        h = _merge_clearance(url, h)
        return await _do_request("GET", url, h, params=params, timeout=timeout, proxy=proxy)

    async def post(
        self,
        url: str,
        body: Any = None,
        headers: Optional[dict[str, str]] = None,
        referer: Optional[str] = None,
        timeout: float = 15.0,
        proxy: Optional[str] = None,
        content_type: str = "application/x-www-form-urlencoded",
    ) -> SpResponse:
        if proxy is None:
            from app.utils.warp import warp_proxy
            proxy = warp_proxy()

        h = {**{"User-Agent": UA, "Content-Type": content_type}, **(headers or {})}
        if referer:
            h["Referer"] = referer
        h = _merge_clearance(url, h)

        if isinstance(body, (dict, list)):
            data = orjson.dumps(body)
            h["Content-Type"] = "application/json"
        elif isinstance(body, str):
            data = body.encode()
        else:
            data = body

        return await _do_request("POST", url, h, data=data, timeout=timeout, proxy=proxy)


app = _App()


async def safe_get(
    url: str,
    headers: Optional[dict[str, str]] = None,
    referer: Optional[str] = None,
    timeout: float = 15.0,
    params: Optional[dict] = None,
    cloudflare: bool = False,
    flare_timeout: float = 60.0,
    proxy: Optional[str] = None,
) -> SpResponse:
    """
    Cloudflare-aware GET.
    Fast path: plain request. On CF challenge → FlareSolverr.
    WARP proxy auto-injected from context.
    """
    if proxy is None:
        from app.utils.warp import warp_proxy
        proxy = warp_proxy()

    if not cloudflare:
        try:
            res = await app.get(url, headers=headers, referer=referer,
                                timeout=timeout, params=params, proxy=proxy)
            if not _looks_like_cloudflare(res.status, res.text):
                return res
        except Exception:
            pass

    if _is_flaresolverr_configured():
        from app.utils.warp import warp_flaresolverr
        solved = await _flaresolverr_get(url, timeout=flare_timeout,
                                         endpoint_override=warp_flaresolverr())
        if solved:
            set_clearance(url, solved.cookie_header, UA)
            return solved

    return await app.get(url, headers=headers, referer=referer,
                         timeout=timeout, params=params, proxy=proxy)


# ── FlareSolverr ───────────────────────────────────────────────────────────────

_flare_endpoints: list[str] = []
_flare_idx = 0


def _init_flare() -> None:
    global _flare_endpoints
    raw = _settings.flaresolverr_url
    if raw:
        _flare_endpoints = [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]


def _is_flaresolverr_configured() -> bool:
    if not _flare_endpoints:
        _init_flare()
    return bool(_flare_endpoints)


def _next_flare_endpoint() -> Optional[str]:
    global _flare_idx
    if not _flare_endpoints:
        return None
    ep = _flare_endpoints[_flare_idx % len(_flare_endpoints)]
    _flare_idx += 1
    return ep


async def _flaresolverr_get(
    url: str,
    timeout: float = 60.0,
    endpoint_override: Optional[str] = None,
) -> Optional[SpResponse]:
    ep = endpoint_override or _next_flare_endpoint()
    if not ep:
        return None
    try:
        payload = orjson.dumps({
            "cmd": "request.get",
            "url": url,
            "maxTimeout": int(timeout * 1000),
        })
        client = await get_client()
        resp = await client.post(
            f"{ep}/v1",
            content=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout + 5,
        )
        data = orjson.loads(resp.text)
        sol = data.get("solution", {})
        html = sol.get("response", "")
        cookies = "; ".join(
            f"{c['name']}={c['value']}"
            for c in sol.get("cookies", [])
            if c.get("name") and c.get("value")
        )
        return _build_response(sol.get("status", 200), html, url, cookies)
    except Exception:
        return None


# ── Retry helper ───────────────────────────────────────────────────────────────

async def retry(times: int, coro_fn) -> Any:
    """Run coro_fn() up to `times` times; return result or None."""
    for i in range(times):
        try:
            result = await coro_fn()
            if result is not None:
                return result
        except Exception:
            if i < times - 1:
                await asyncio.sleep(0.3 * (i + 1))
    return None
