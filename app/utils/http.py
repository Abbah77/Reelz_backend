"""
Async HTTP layer — mirrors the Node utils/http.ts safeGet / app.get / app.post.

Key design:
- Single shared httpx.AsyncClient with HTTP/2 + connection pooling.
- Per-domain Cloudflare cookie jar (cf_clearance replay).
- FlareSolverr integration when Cloudflare challenge detected.
- WARP SOCKS5 proxy routing — automatically injected from the per-request
  ContextVar set by run_with_warp(). Providers don't need to pass a proxy
  arg; just calling app.get() inside a WARP context is enough.
- BeautifulSoup4 document parsing (lxml backend — fastest available).
- orjson for JSON (5-10x faster than stdlib json).

WARP integration:
  warp_proxy() returns the active SOCKS5 URL for the current request, or None.
  We call it at the start of every public HTTP method so the proxy is always
  transparently injected. A provider written before WARP existed gets WARP for
  free with zero code changes.
"""
from __future__ import annotations

import asyncio
import re
import time
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

# ── Shared client (HTTP/2, keep-alive, pooled) ────────────────────────────────
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


# ── Cookie jar (Cloudflare clearance per hostname) ────────────────────────────
_cookie_jar: dict[str, dict[str, str]] = {}  # host -> {cookie, userAgent}


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


# ── SpResponse — mirrors Node's SpResponse ────────────────────────────────────

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
    max_redirects: int = 10,
    proxy: Optional[str] = None,
) -> SpResponse:
    """Raw HTTP call via the shared httpx client."""
    client = await get_client()

    if proxy:
        # One-off proxied client for WARP SOCKS5 connections.
        # HTTP/2 disabled — SOCKS proxy doesn't multiplex cleanly.
        async with httpx.AsyncClient(
            http2=False,
            follow_redirects=True,
            timeout=httpx.Timeout(timeout),
            proxies={"all://": proxy},
            headers={"User-Agent": UA},
        ) as proxied:
            resp = await proxied.request(
                method, url, headers=headers, content=data, params=params
            )
    else:
        resp = await client.request(
            method, url, headers=headers, content=data, params=params,
            timeout=timeout,
        )

    text = resp.text
    resp_headers = {k.lower(): v for k, v in resp.headers.items()}
    return _build_response(resp.status_code, text, str(resp.url), "", resp_headers)


# ── Public helpers ─────────────────────────────────────────────────────────────

class _App:
    """
    mirrors Node's `app` object: app.get / app.post

    WARP is injected automatically — if a WARP context is active (set by
    run_with_warp() in the orchestrator), warp_proxy() returns the right
    SOCKS5 URL and it's used for this call. No provider changes needed.
    """

    async def get(
        self,
        url: str,
        headers: Optional[dict[str, str]] = None,
        referer: Optional[str] = None,
        timeout: float = 15.0,
        params: Optional[dict] = None,
        max_redirects: int = 10,
        proxy: Optional[str] = None,
    ) -> SpResponse:
        # Auto-inject WARP proxy from context if caller didn't supply one
        if proxy is None:
            from app.utils.warp import warp_proxy
            proxy = warp_proxy()

        h = {**{"User-Agent": UA}, **(headers or {})}
        if referer:
            h["Referer"] = referer
        h = _merge_clearance(url, h)
        return await _do_request("GET", url, h, params=params, timeout=timeout,
                                  max_redirects=max_redirects, proxy=proxy)

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
        # Auto-inject WARP proxy from context if caller didn't supply one
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
    Cloudflare-aware GET — mirrors Node's safeGet.
    Fast path: plain request. On CF challenge → FlareSolverr.
    WARP proxy is auto-injected from context if not explicitly passed.
    """
    # Auto-inject WARP proxy from context
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

    # Try FlareSolverr (WARP-backed endpoint if active)
    if _is_flaresolverr_configured():
        from app.utils.warp import warp_flaresolverr
        warp_fs = warp_flaresolverr()
        solved = await _flaresolverr_get(url, timeout=flare_timeout, endpoint_override=warp_fs)
        if solved:
            set_clearance(url, solved.cookie_header, UA)
            return solved

    # Last resort — plain request
    return await app.get(url, headers=headers, referer=referer,
                         timeout=timeout, params=params, proxy=proxy)


# ── FlareSolverr ──────────────────────────────────────────────────────────────

_flare_endpoints: list[str] = []
_flare_idx = 0
_flare_lock = asyncio.Lock()


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
    """
    endpoint_override: pass the WARP-backed FlareSolverr URL to route the
    browser solve through WARP egress (needed for hosts that block datacenter IPs
    at the Cloudflare challenge step, not just on the CDN).
    """
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


# ── Tiny retry helper ─────────────────────────────────────────────────────────

async def retry(times: int, coro_fn):
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
