"""
ENGINE/providers/Shorts/registry.py — Shorts provider registry.

THE ONLY FILE THAT KNOWS SHORTS PROVIDERS EXIST.

Provider ID range: R-301 to R-399

R-301 (TMDB Trailers) is DISABLED — it emits YouTube watch URLs which
ExoPlayer cannot play directly. When R-301 results reach the Android
client they trigger immediate playback errors, markDead() fires, and
those slots are wasted. Move R301Provider back to ACTIVE only once a
YouTube→direct-MP4 resolver is wired in.
"""
from __future__ import annotations

from ENGINE.providers.base import Provider
from ENGINE.providers.Shorts.R_301.R_301 import R301Provider
from ENGINE.providers.Shorts.R_302.R_302 import R302Provider

ACTIVE: list[Provider] = [
    R302Provider(),   # Archive.org TikToks — direct MP4, works with ExoPlayer
]

DISABLED: list[Provider] = [
    R301Provider(),   # TMDB Trailers — YouTube URLs, not playable by ExoPlayer
]

_registry: list[Provider] = []
_ready = False


def init() -> None:
    global _ready
    if _ready:
        return
    _registry.extend(ACTIVE)
    _ready = True


def get_all() -> list[Provider]:
    return list(_registry)
