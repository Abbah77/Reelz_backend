"""
ENGINE/providers/Shorts/registry.py — Shorts provider registry.

THE ONLY FILE THAT KNOWS SHORTS PROVIDERS EXIST.

Provider ID range: R-301 to R-399

R-301 (TMDB Trailers) is DISABLED — it emits YouTube watch URLs which
ExoPlayer cannot play directly. When R-301 results reach the Android
client they trigger immediate playback errors, markDead() fires, and
those slots are wasted. Move R301Provider back to ACTIVE only once a
YouTube→direct-MP4 resolver is wired in.

R-302 (Archive.org TikToks) was fixed: the old naming assumption
({identifier}.mp4) broke when archive.org changed item file layouts.
Now resolves the real filename via the metadata API before building
the download URL. Thumbnail uses /services/img/{identifier} (still valid).

R-303 (iFunny WeFeed) scrapes the SeoCloud WeFeed BFF used by the
iFunny Android/web client. Returns direct MP4 URLs from themed group
feeds. No auth required; requires Origin + X-Client-Info headers.
"""
from __future__ import annotations

from ENGINE.providers.base import Provider
from ENGINE.providers.Shorts.R_301.R_301 import R301Provider   # TMDB Trailers
from ENGINE.providers.Shorts.R_302.R_302 import R302Provider   # Archive.org TikToks
from ENGINE.providers.Shorts.R_303.R_303 import R303Provider   # iFunny WeFeed Shorts

ACTIVE: list[Provider] = [
    R302Provider(),   # Archive.org TikToks — direct MP4 via metadata resolve
    R303Provider(),   # iFunny WeFeed — direct MP4 from group feeds
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
