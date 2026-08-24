"""
ENGINE/tools/hls.py — HLS master playlist parser.

Given a master .m3u8 URL, fetches and parses it to extract all quality-specific
index.m3u8 URLs with their resolution/bandwidth metadata.

Usage:
    from ENGINE.tools.hls import resolve_master

    qualities = await resolve_master("https://example.com/master.m3u8")
    # Returns list of dicts:
    # [{"quality": "1080p", "url": "https://...index_1080p.m3u8", "bandwidth": 5000000}, ...]
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin

from ENGINE.tools.http import get_client, UA

_QUALITY_MAP = {
    2160: "4K",
    1080: "1080p",
    720:  "720p",
    480:  "480p",
    360:  "360p",
    240:  "240p",
    144:  "144p",
}


def _height_to_label(height: int) -> str:
    """Map pixel height to quality label. Nearest match."""
    if height >= 2000:
        return "4K"
    for h, label in _QUALITY_MAP.items():
        if height >= h:
            return label
    return f"{height}p"


def _bandwidth_to_label(bandwidth: int) -> str:
    """Estimate quality from bandwidth when resolution is absent."""
    if bandwidth >= 8_000_000:
        return "4K"
    if bandwidth >= 4_000_000:
        return "1080p"
    if bandwidth >= 2_000_000:
        return "720p"
    if bandwidth >= 800_000:
        return "480p"
    if bandwidth >= 400_000:
        return "360p"
    return "240p"


def _parse_master(content: str, base_url: str) -> list[dict]:
    """
    Parse HLS master playlist content.
    Returns list of {quality, url, bandwidth, resolution} sorted best→worst.
    """
    lines = content.splitlines()
    results = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXT-X-STREAM-INF"):
            attrs = line[len("#EXT-X-STREAM-INF:"):]
            bandwidth = 0
            resolution = None
            height = 0

            bw_m = re.search(r"BANDWIDTH=(\d+)", attrs)
            if bw_m:
                bandwidth = int(bw_m.group(1))

            res_m = re.search(r"RESOLUTION=(\d+)x(\d+)", attrs)
            if res_m:
                width  = int(res_m.group(1))
                height = int(res_m.group(2))
                resolution = f"{width}x{height}"

            quality = _height_to_label(height) if height else _bandwidth_to_label(bandwidth)

            # Next non-comment line is the URI
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].strip().startswith("#")):
                j += 1
            if j < len(lines):
                uri = lines[j].strip()
                if uri:
                    # Resolve relative URLs
                    full_url = uri if uri.startswith("http") else urljoin(base_url, uri)
                    results.append({
                        "quality":    quality,
                        "url":        full_url,
                        "bandwidth":  bandwidth,
                        "resolution": resolution,
                    })
            i = j + 1
        else:
            i += 1

    # Sort best quality first, deduplicate by quality label (keep highest bandwidth)
    seen: dict[str, dict] = {}
    for r in results:
        q = r["quality"]
        if q not in seen or r["bandwidth"] > seen[q]["bandwidth"]:
            seen[q] = r

    ordered = sorted(seen.values(), key=lambda x: x["bandwidth"], reverse=True)
    return ordered


def _is_master(content: str) -> bool:
    """Return True if this looks like a master playlist (has EXT-X-STREAM-INF)."""
    return "#EXT-X-STREAM-INF" in content


async def resolve_master(
    url: str,
    headers: Optional[dict] = None,
) -> list[dict]:
    """
    Fetch a .m3u8 URL.
    - If it's a master playlist: parse and return all quality variants.
    - If it's already a media playlist (index.m3u8): return as single entry.

    Returns [] on any error.
    """
    try:
        h = {"User-Agent": UA, **(headers or {})}
        client = await get_client()
        resp = await client.get(url, headers=h, timeout=15)
        if resp.status_code >= 400:
            return []
        content = resp.text

        if not _is_master(content):
            # Already a specific quality playlist — return as-is with unknown quality
            return [{"quality": "Auto", "url": url, "bandwidth": 0, "resolution": None}]

        return _parse_master(content, url)

    except Exception:
        return []


async def resolve_master_from_stream(
    stream_url: str,
    stream_type: str,
    headers: Optional[dict] = None,
) -> list[dict]:
    """
    Used by the download manager to get quality variants from a stream provider's URL.
    If type is 'mp4': returns single item with that URL.
    If type is 'm3u8'/'hls': resolves master → quality list.
    """
    if stream_type in ("mp4", "mkv"):
        return [{"quality": "Auto", "url": stream_url, "bandwidth": 0, "resolution": None}]
    return await resolve_master(stream_url, headers=headers)
