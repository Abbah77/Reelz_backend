"""
Shared video-host extractor layer — Python port of Node's utils/hostextractors.ts.

Handles:
- Dean Edwards p,a,c,k,e,d unpacker
- StreamWish / Filelions / VidHide family (packed-JS m3u8 players)
- HubCloud  (multiple direct download / m3u8 links)
- GDFlix    (direct / instant / pixeldrain / CF links)
- Pixeldrain passthrough
- Generic m3u8 / mp4 link extraction
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, urljoin

from app.utils.http import app, safe_get, SpResponse, UA
from app.models import Stream, Subtitle


@dataclass
class HostResult:
    streams: list[Stream] = field(default_factory=list)
    subtitles: list[Subtitle] = field(default_factory=list)


def empty_result() -> HostResult:
    return HostResult()


# ── Dean Edwards p,a,c,k,e,d unpacker ────────────────────────────────────────

_PACKED_RE = re.compile(
    r"(eval\(function\(p,a,c,k,e,d?\)\{.*?\}\([^)]*\)\))",
    re.DOTALL,
)
_PACKED_ARGS_RE = re.compile(
    r"\}\s*\(\s*'((?:\\.|[^'\\])*)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'((?:\\.|[^'\\])*)'\s*\.split\('\\|'\)",
    re.DOTALL,
)


def get_packed(text: str) -> Optional[str]:
    scan = text[:512 * 1024] if len(text) > 512 * 1024 else text
    m = _PACKED_RE.search(scan)
    return m.group(1) if m else None


def get_and_unpack(text: str) -> str:
    packed = get_packed(text) or text
    m = _PACKED_ARGS_RE.search(packed)
    if not m:
        return ""

    payload = m.group(1)
    radix = int(m.group(2))
    count = int(m.group(3))
    words = m.group(4).split("|")

    if not (1 <= radix <= 62):
        return ""

    payload = payload.replace("\\'", "'").replace("\\\\", "\\")

    def unbase(n: int) -> str:
        chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        if n == 0:
            return "0"
        result = ""
        while n:
            result = chars[n % radix] + result
            n //= radix
        return result

    dictionary: dict[str, str] = {}
    for i in range(count - 1, -1, -1):
        key = unbase(i)
        dictionary[key] = words[i] if i < len(words) and words[i] else key

    return re.sub(r"\b\w+\b", lambda mo: dictionary.get(mo.group(0), mo.group(0)), payload)


# ── Host helpers ──────────────────────────────────────────────────────────────

def get_host(url: str) -> str:
    try:
        return urlparse(url).hostname.lower()
    except Exception:
        return ""


def get_base_url(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def make_absolute(base: str, href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    return urljoin(base, href)


# ── StreamWish / Filelions / VidHide / Ridoo family ──────────────────────────

_PACKED_PLAYER_HOSTS = {
    "streamwish", "filelions", "vidhide", "vidhidepro", "hdstream4u",
    "dwish", "dlions", "alions", "mwish", "embedwish", "swhoi", "sfastwish",
    "ridoo", "streamruby", "rubystm", "rubyvid", "rapidplayers", "luluvdo",
    "movearnpre", "smoothpre", "streamvid", "cdnwish", "wishfast", "kswplayer",
    "flaswish", "obeywish", "streamewish", "animezia", "server2", "filemoon",
    "doodstream", "dood", "ds2play", "vidguard", "vgfplay",
}


def is_packed_player_host(host: str) -> bool:
    return any(h in host for h in _PACKED_PLAYER_HOSTS)


_HLS_RE = re.compile(r'(?:file|src)\s*:\s*["\']([^"\']*\.m3u8[^"\']*)["\']', re.IGNORECASE)
_MP4_RE = re.compile(r'(?:file|src)\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']', re.IGNORECASE)
_TRACKS_RE = re.compile(
    r'\{[^}]*kind\s*:\s*["\'](?:captions|subtitles)["\'][^}]*file\s*:\s*["\']([^"\']+)["\'][^}]*label\s*:\s*["\']([^"\']+)["\'][^}]*\}',
    re.IGNORECASE | re.DOTALL,
)
_TRACK2_RE = re.compile(
    r'\{[^}]*file\s*:\s*["\']([^"\']+)["\'][^}]*label\s*:\s*["\']([^"\']+)["\'][^}]*kind\s*:\s*["\'](?:captions|subtitles)["\'][^}]*\}',
    re.IGNORECASE | re.DOTALL,
)


async def _resolve_packed_player(url: str, referer: Optional[str], server_name: str) -> HostResult:
    result = empty_result()
    try:
        base = get_base_url(url)
        res = await app.get(url, referer=referer or base, headers={"User-Agent": UA})
        body = res.text

        # Try to unpack
        unpacked = get_and_unpack(body)
        source = unpacked if unpacked else body

        # Extract HLS
        m = _HLS_RE.search(source)
        if m:
            link = m.group(1)
            result.streams.append(Stream(
                server=server_name,
                link=link,
                type="m3u8",
                quality=None,        # caller will backfill from anchor context
                headers={"Referer": referer or base, "Origin": base},
            ))

        # Extract MP4 fallback
        if not result.streams:
            mp4m = _MP4_RE.search(source)
            if mp4m:
                result.streams.append(Stream(
                    server=server_name,
                    link=mp4m.group(1),
                    type="mp4",
                    headers={"Referer": referer or base},
                ))

        # Extract subtitles
        for pat in (_TRACKS_RE, _TRACK2_RE):
            for tm in pat.finditer(source):
                result.subtitles.append(Subtitle(language=tm.group(2), url=tm.group(1)))
    except Exception:
        pass
    return result


# ── HubCloud ──────────────────────────────────────────────────────────────────

_HUBCLOUD_HOSTS = {"hubcloud", "hubdrive"}
_GDRIVE_RE = re.compile(r'href=["\']([^"\']*drive\.google\.com[^"\']+)["\']')
_DIRECT_DL_RE = re.compile(r'href=["\']([^"\']*\.(mp4|mkv|m3u8)[^"\']*)["\']', re.IGNORECASE)


async def _resolve_hubcloud(url: str, server_name: str) -> HostResult:
    result = empty_result()
    try:
        base = get_base_url(url)
        res = await safe_get(url, referer=base)
        body = res.text

        # Look for direct download links
        for m in _DIRECT_DL_RE.finditer(body):
            link = m.group(1)
            ext = m.group(2).lower()
            result.streams.append(Stream(
                server=f"{server_name} HubCloud",
                link=link,
                type="m3u8" if ext == "m3u8" else "mp4",
                headers={"Referer": base},
            ))

        # Follow "Download" button links
        soup = res.document
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True).lower()
            if any(k in text for k in ("download", "direct", "server")):
                if href.startswith("http"):
                    # Follow one hop
                    try:
                        r2 = await app.get(href, referer=url, timeout=10)
                        for m in _DIRECT_DL_RE.finditer(r2.text):
                            result.streams.append(Stream(
                                server=f"{server_name} HubCloud-DL",
                                link=m.group(1),
                                type="m3u8" if m.group(2).lower() == "m3u8" else "mp4",
                                headers={"Referer": href},
                            ))
                    except Exception:
                        pass
    except Exception:
        pass
    return result


# ── GDFlix ────────────────────────────────────────────────────────────────────

_GDFLIX_HOSTS = {"gdflix", "gdflix2"}
_PIXELDRAIN_RE = re.compile(r'pixeldrain\.com/u/([a-zA-Z0-9]+)')
_CF_URL_RE = re.compile(r'https?://[^\s"\'<>]+\.(?:m3u8|mp4|mkv)[^\s"\'<>]*', re.IGNORECASE)


async def _resolve_gdflix(url: str, server_name: str) -> HostResult:
    result = empty_result()
    try:
        base = get_base_url(url)
        res = await safe_get(url, referer=base, timeout=20)
        body = res.text
        soup = res.document

        # Pixeldrain links
        for m in _PIXELDRAIN_RE.finditer(body):
            file_id = m.group(1)
            pd_url = f"https://pixeldrain.com/api/file/{file_id}?download"
            result.streams.append(Stream(
                server=f"{server_name} [Pixeldrain]",
                link=pd_url,
                type="mp4",
            ))

        # Direct CF/CDN links
        for m in _CF_URL_RE.finditer(body):
            link = m.group(0)
            if "pixeldrain" not in link:
                ext = "m3u8" if ".m3u8" in link.lower() else "mp4"
                result.streams.append(Stream(
                    server=f"{server_name} [Direct]",
                    link=link,
                    type=ext,
                    headers={"Referer": base},
                ))

        # Follow "Instant DL" / "Direct DL" buttons
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True).lower()
            if any(k in text for k in ("instant", "direct", "download", "server 1", "server 2")):
                abs_href = make_absolute(base, href)
                try:
                    r2 = await app.get(abs_href, referer=url, timeout=10, max_redirects=0)
                    loc = r2.response_headers.get("location", "") or r2.response_headers.get("hx-redirect", "")
                    if loc and any(ext in loc.lower() for ext in (".mp4", ".mkv", ".m3u8")):
                        ext = "m3u8" if ".m3u8" in loc.lower() else "mp4"
                        result.streams.append(Stream(
                            server=f"{server_name} [GDFlix-DL]",
                            link=loc,
                            type=ext,
                            headers={"Referer": abs_href},
                        ))
                except Exception:
                    pass
    except Exception:
        pass
    return result


# ── Generic extractor fallback ────────────────────────────────────────────────

_GENERIC_HLS_RE = re.compile(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', re.IGNORECASE)
_GENERIC_MP4_RE = re.compile(r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*', re.IGNORECASE)


async def _resolve_generic(url: str, server_name: str) -> HostResult:
    result = empty_result()
    try:
        base = get_base_url(url)
        res = await safe_get(url, referer=base, timeout=15)
        body = res.text
        unpacked = get_and_unpack(body)
        source = unpacked if unpacked else body

        for m in _GENERIC_HLS_RE.finditer(source):
            result.streams.append(Stream(
                server=server_name,
                link=m.group(0),
                type="m3u8",
                headers={"Referer": base},
            ))
        if not result.streams:
            for m in _GENERIC_MP4_RE.finditer(source):
                result.streams.append(Stream(
                    server=server_name,
                    link=m.group(0),
                    type="mp4",
                    headers={"Referer": base},
                ))
    except Exception:
        pass
    return result


# ── Main dispatch ─────────────────────────────────────────────────────────────

async def load_extractor(url: str, referer: str, server_name: str = "Server") -> HostResult:
    """
    Inspect the URL host and dispatch to the matching resolver.
    Every resolver is exception-safe: on failure returns empty HostResult.
    """
    host = get_host(url)
    if not host:
        return empty_result()

    if any(h in host for h in _HUBCLOUD_HOSTS):
        return await _resolve_hubcloud(url, server_name)

    if any(h in host for h in _GDFLIX_HOSTS):
        return await _resolve_gdflix(url, server_name)

    if is_packed_player_host(host):
        return await _resolve_packed_player(url, referer, server_name)

    # Pixeldrain direct
    if "pixeldrain" in host:
        m = _PIXELDRAIN_RE.search(url)
        if m:
            return HostResult(streams=[Stream(
                server=f"{server_name} [Pixeldrain]",
                link=f"https://pixeldrain.com/api/file/{m.group(1)}?download",
                type="mp4",
            )])

    return await _resolve_generic(url, server_name)
