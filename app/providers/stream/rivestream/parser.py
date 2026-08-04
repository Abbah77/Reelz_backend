"""
providers/stream/rivestream/parser.py — RiveStream response parsing.

Isolated here so provider.py stays clean and parser logic is testable.
"""
from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import unquote

from app.schemas.provider import Stream

_APP_RE = re.compile(r'src="(/[^"]*_app[^"]*)"')
_KEY_ARRAY_RE = re.compile(r'let\s+c\s*=\s*(\[[^\]]*\])')
_STRING_RE = re.compile(r'"([^"]+)"')


def extract_app_script_path(html: str) -> Optional[str]:
    m = _APP_RE.search(html)
    return m.group(1) if m else None


def extract_key_list(js: str) -> list[str]:
    for km in _KEY_ARRAY_RE.finditer(js):
        arr_str = km.group(1)
        if len(arr_str) > 2:
            return _STRING_RE.findall(arr_str)
    return []


def parse_sources(sources: list[dict], label_prefix: str) -> list[Stream]:
    """Parse the data.sources array from a RiveStream stream response."""
    streams: list[Stream] = []
    if not isinstance(sources, list):
        return streams

    for src in sources:
        src_name: str = src.get("source", "")
        label = (
            f"{label_prefix} {src_name}[{src.get('quality', '')}]"
            if "asiacloud" in src_name.lower()
            else f"{label_prefix} {src_name}"
        )
        url: str = src.get("url", "")
        if not url:
            continue

        try:
            if "proxy?url=" in url:
                fully_decoded = unquote(url)
                encoded_url = fully_decoded.split("proxy?url=")[1].split("&headers=")[0]
                decoded_url = unquote(encoded_url)
                encoded_headers = (
                    fully_decoded.split("&headers=")[1]
                    if "&headers=" in fully_decoded
                    else ""
                )
                try:
                    headers_map: dict = json.loads(unquote(encoded_headers))
                except Exception:
                    headers_map = {}

                video_headers = {
                    k: v for k, v in headers_map.items()
                    if k in ("Referer", "Origin", "User-Agent")
                }
                ext = "m3u8" if ".m3u8" in decoded_url.lower() else "mp4"
                streams.append(Stream(
                    server=label,
                    link=decoded_url,
                    type=ext,
                    quality="1080p",
                    headers=video_headers,
                ))
            else:
                ext = "m3u8" if ".m3u8" in url.lower() else "mp4"
                streams.append(Stream(
                    server=f"{label} (VLC)",
                    link=url,
                    type=ext,
                    quality="1080p",
                ))
        except Exception:
            pass

    return streams
