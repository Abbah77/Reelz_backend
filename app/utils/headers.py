"""
utils/headers.py — common browser header presets.

Providers import these instead of duplicating header dicts.
"""
from __future__ import annotations

from app.clients.http import UA


def browser_headers(referer: str = "", origin: str = "") -> dict[str, str]:
    h: dict[str, str] = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    }
    if referer:
        h["Referer"] = referer
    if origin:
        h["Origin"] = origin
    return h


def json_headers(referer: str = "", origin: str = "") -> dict[str, str]:
    h: dict[str, str] = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        h["Referer"] = referer
    if origin:
        h["Origin"] = origin
    return h


def video_headers(referer: str, origin: str = "") -> dict[str, str]:
    """Headers for a direct video/m3u8 fetch (Range-ready)."""
    h: dict[str, str] = {
        "User-Agent": UA,
        "Referer": referer,
        "Accept": "*/*",
    }
    if origin:
        h["Origin"] = origin
    return h
