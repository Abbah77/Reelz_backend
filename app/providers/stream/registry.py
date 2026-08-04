"""
providers/stream/registry.py — stream provider registry.

THIS IS THE ONLY FILE THAT KNOWS STREAM PROVIDERS EXIST.

Add a provider:    one import + one line in ACTIVE.
Delete a provider: delete the folder + remove one line here.
Nothing else ever changes.

Priority order matters — providers listed first are tried first
in the "first-wins" race. Put fastest/most reliable at the top.
"""
from __future__ import annotations

from app.providers.base import Provider

# ── Active stream providers ────────────────────────────────────────────────────
# To add: import your provider class and append it to ACTIVE.
# To remove: delete the provider folder and remove the line below.

from app.providers.stream.direct_api.provider import (
    TwoEmbedProvider,
    VidFastProvider,
    VidRockProvider,
    HexaSUProvider,
    AllMovieLandProvider,
    XpassProvider,
    VaplayerV2Provider,
    DahmerMoviesProvider,
)
from app.providers.stream.rivestream.provider import RiveStreamProvider
from app.providers.stream.primevids.provider import PrimeVidsProvider
from app.providers.stream.specialty.provider import (
    KissKhProvider,
    CastleProvider,
    HDRezkaProvider,
)
from app.providers.stream.anime.provider import (
    AniZoneProvider,
    AniNekoProvider,
    AnimeNoSubProvider,
    AnimeWorldProvider,
)
from app.providers.stream.indian.provider import (
    VegaMoviesProvider,
    HdHub4uProvider,
    FourKHdHubProvider,
    Movies4uProvider,
    RogMoviesProvider,
    MultiMoviesProvider,
    UhdMoviesProvider,
    MoviesModProvider,
    TopMoviesProvider,
    BollyflixProvider,
    CineMacityProvider,
)

ACTIVE: list[Provider] = [
    # ── Anime-specific (serve anime only; bail early for non-anime at zero cost) ──
    AniZoneProvider(),
    AniNekoProvider(),
    AnimeNoSubProvider(),
    AnimeWorldProvider(),

    # ── Direct API (fastest — no Cloudflare, pure JSON/API) ──────────────────
    TwoEmbedProvider(),
    VidFastProvider(),
    VidRockProvider(),
    HexaSUProvider(),
    RiveStreamProvider(),
    AllMovieLandProvider(),
    XpassProvider(),
    VaplayerV2Provider(),
    DahmerMoviesProvider(),

    # ── Embed aggregator ─────────────────────────────────────────────────────
    PrimeVidsProvider(),

    # ── Asian drama ───────────────────────────────────────────────────────────
    KissKhProvider(),

    # ── Multi-language / Indian ───────────────────────────────────────────────
    CastleProvider(),
    VegaMoviesProvider(),
    HdHub4uProvider(),
    FourKHdHubProvider(),
    Movies4uProvider(),
    RogMoviesProvider(),
    MultiMoviesProvider(),
    UhdMoviesProvider(),
    MoviesModProvider(),
    TopMoviesProvider(),
    BollyflixProvider(),
    CineMacityProvider(),

    # ── Large multi-audio catalog ─────────────────────────────────────────────
    HDRezkaProvider(),
]

# ── Disabled (dead upstreams — easy to revive by moving back to ACTIVE) ───────
DISABLED: list[Provider] = [
    # VidlinkProvider(),    # dead upstream
]

# ── Internal: populated at startup ────────────────────────────────────────────
_registry: list[Provider] = []
_initialised = False


def init_stream_providers() -> None:
    """Called once at app startup. Safe to call multiple times."""
    global _initialised
    if _initialised:
        return
    _registry.extend(ACTIVE)
    _initialised = True


def get_stream_providers() -> list[Provider]:
    return list(_registry)


def get_stream_providers_for_kind(kind: str) -> list[Provider]:
    return [p for p in _registry if p.serves_kind(kind)]  # type: ignore[arg-type]
