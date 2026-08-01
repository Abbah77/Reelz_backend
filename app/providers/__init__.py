"""
Provider registry — registers all providers in the exact priority order
from the Node extractors/index.ts, with identical kind gating.

Import this module once at app startup to populate the registry.
"""
from __future__ import annotations

from app.providers.base import register, register_disabled, _providers

# ── Anime (run first — they early-return for non-anime content at zero cost) ──
from app.providers.anime import (
    AniZoneProvider,
    AniNekoProvider,
    AnimeNoSubProvider,
    AnimeWorldProvider,
    AllAnimeProvider,
)

# ── Direct-API (no Cloudflare, fastest) ──────────────────────────────────────
from app.providers.direct_api import (
    TwoEmbedProvider,
    VidFastProvider,
    VidRockProvider,
    HexaProvider,
    HexaSUProvider,        # new: real HexaSU API flow (replaces old stub)
    XpassProvider,
    VaplayerProvider,
    VaplayerV2Provider,    # new: correct streamdata.vaplayer.ru endpoint
    DahmerMoviesProvider,
)

# ── PrimeVids ─────────────────────────────────────────────────────────────────
from app.providers.primevids import PrimeVidsProvider

# ── RiveStream ────────────────────────────────────────────────────────────────
from app.providers.rivestream import RiveStreamProvider

# ── AllMovieLand ──────────────────────────────────────────────────────────────
from app.providers.direct_api import AllMovieLandProvider

# ── Specialty ─────────────────────────────────────────────────────────────────
from app.providers.specialty import (
    KissKhProvider,
    CastleProvider,
    HDRezkaProvider,
    VidlinkProvider,
)

# ── Indian / CF-gated ─────────────────────────────────────────────────────────
from app.providers.indian import (
    VegaMoviesProvider,
    HdHub4uProvider,
    FourKHdHubProvider,    # now fully implemented (was stub)
    Movies4uProvider,      # now fully implemented (was stub)
    RogMoviesProvider,
    MultiMoviesProvider,
    UhdMoviesProvider,
    MoviesModProvider,
    TopMoviesProvider,
    BollyflixProvider,
    CineMacityProvider,
)

# ── Subtitles ─────────────────────────────────────────────────────────────────
from app.providers.subtitles import SubtitleApiProvider, WyzieSubsProvider


def init_providers() -> None:
    """
    Called once at startup. Registers providers in priority order.
    Mirrors the Node PROVIDER_KINDS kind-gating map exactly.
    """
    if _providers:
        return  # already initialised (e.g. reload)

    # === Anime providers (first — early-return for non-anime at zero cost) ===
    _reg(AniZoneProvider(), kinds=["anime"])
    _reg(AniNekoProvider(), kinds=["anime"])
    _reg(AnimeNoSubProvider(), kinds=["anime"])
    _reg(AnimeWorldProvider())          # intentionally no kind gate (serves all)
    # AllAnime: disabled by default (NEED_CAPTCHA on datacenter IPs) — register as disabled
    register_disabled(AllAnimeProvider())

    # === Direct-API (fast, no CF) ===
    _reg(TwoEmbedProvider())
    _reg(VidFastProvider())
    _reg(VidRockProvider())
    _reg(HexaSUProvider())         # new: real HexaSU 4-step API flow
    _reg(RiveStreamProvider())
    _reg(AllMovieLandProvider())
    _reg(XpassProvider())
    _reg(VaplayerV2Provider())     # new: corrected streamdata.vaplayer.ru endpoint
    _reg(DahmerMoviesProvider())
    # PrimeVids: embed aggregator (movie + series + asian)
    _reg(PrimeVidsProvider(), kinds=["movie", "series", "asian"])
    # Old stub providers kept as disabled for reference
    register_disabled(HexaProvider())
    register_disabled(VaplayerProvider())

    # === Asian drama ===
    _reg(KissKhProvider(), kinds=["asian"])

    # === CastleTV (Indian multi-lang — also anime) ===
    _reg(CastleProvider(), kinds=["movie", "series", "asian", "anime"])

    # === Indian scraper / CF-gated ===
    _reg(VegaMoviesProvider(), kinds=["movie", "series", "asian"])
    _reg(HdHub4uProvider(), kinds=["movie", "series", "asian"])
    _reg(FourKHdHubProvider(), kinds=["movie", "series", "asian"])
    _reg(Movies4uProvider(), kinds=["movie", "series", "asian"])
    _reg(RogMoviesProvider(), kinds=["movie", "series", "asian"])
    _reg(MultiMoviesProvider(), kinds=["movie", "series", "asian"])
    _reg(UhdMoviesProvider(), kinds=["movie", "series", "asian"])
    _reg(MoviesModProvider(), kinds=["movie", "series", "asian"])
    _reg(TopMoviesProvider(), kinds=["movie", "series", "asian"])
    _reg(BollyflixProvider(), kinds=["movie", "series", "asian"])
    _reg(CineMacityProvider(), kinds=["movie", "series", "asian"])

    # === HDRezka (large multi-audio catalog) ===
    _reg(HDRezkaProvider(), kinds=["movie", "series", "asian"])

    # === Subtitles ===
    _reg(SubtitleApiProvider())
    _reg(WyzieSubsProvider())

    # === Disabled (dead upstreams — kept for easy revival) ===
    register_disabled(VidlinkProvider())


def _reg(provider, kinds=None) -> None:
    if kinds:
        provider.kinds = kinds
    register(provider)


# ── Torrent providers — loaded on demand (TORRENT_ENABLED=1) ─────────────────
# The import itself handles registration when the env var is set.
try:
    import app.providers.torrent  # noqa: F401
except Exception:
    pass
