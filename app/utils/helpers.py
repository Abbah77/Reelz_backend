"""
utils/helpers.py — small utility functions shared across layers.
No business logic. No HTTP. No provider imports.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse


# ── URL normalisation ──────────────────────────────────────────────────────────

def norm_url(url: str) -> str:
    """Strip query string + fragment for dedup purposes."""
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}"
    except Exception:
        return url


# ── Language detection from server label ──────────────────────────────────────

_LANG_MAP = [
    ("hindi",    "Hindi"),
    ("tamil",    "Tamil"),
    ("telugu",   "Telugu"),
    ("malayalam","Malayalam"),
    ("kannada",  "Kannada"),
    ("bengali",  "Bengali"),
    ("marathi",  "Marathi"),
    ("punjabi",  "Punjabi"),
    ("korean",   "Korean"),
    ("kor",      "Korean"),
    ("japanese", "Japanese"),
    ("jpn",      "Japanese"),
    ("chinese",  "Chinese"),
    ("mandarin", "Chinese"),
    ("french",   "French"),
    ("fra",      "French"),
    ("spanish",  "Spanish"),
    ("esp",      "Spanish"),
    ("arabic",   "Arabic"),
    ("ara",      "Arabic"),
    ("dubbed",   "Dubbed"),
    ("dub",      "Dubbed"),
]


def lang_label(server: str) -> str:
    s = server.lower()
    for key, label in _LANG_MAP:
        if key in s:
            return label
    return "English"


# ── Quality normalisation ──────────────────────────────────────────────────────

_QUALITY_NORM = {
    "2160p": "2160p", "4k": "2160p", "uhd": "2160p",
    "1080p": "1080p", "fhd": "1080p",
    "720p":  "720p",  "hd":  "720p",
    "480p":  "480p",  "sd":  "480p",
    "360p":  "360p",
    "240p":  "240p",
}

RESOLUTION_ORDER = ["2160p", "1080p", "720p", "480p", "360p", "240p"]


def normalise_quality(raw: Optional[str]) -> str:
    if not raw:
        return "unknown"
    q = raw.lower().strip()
    for k, v in _QUALITY_NORM.items():
        if k in q:
            return v
    m = re.search(r"(\d{3,4})", q)
    if m:
        h = int(m.group(1))
        if h >= 2000: return "2160p"
        if h >= 900:  return "1080p"
        if h >= 600:  return "720p"
        if h >= 420:  return "480p"
        if h >= 300:  return "360p"
        return "240p"
    return raw.strip() or "unknown"


def fmt_size(b: Optional[int]) -> Optional[str]:
    if not b:
        return None
    for unit, div in (("GB", 1_073_741_824), ("MB", 1_048_576), ("KB", 1_024)):
        if b >= div:
            return f"{b/div:.1f} {unit}"
    return f"{b} B"
