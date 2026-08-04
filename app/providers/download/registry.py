"""
providers/download/registry.py — download provider registry.

THE ONLY FILE that knows download providers exist.
Add: one import + one line in ACTIVE.
Remove: delete folder + remove one line.
"""
from __future__ import annotations

from app.providers.base import Provider
from app.providers.download.indian.provider import (
    VegaMoviesDownloadProvider,
    HdHub4uDownloadProvider,
    FourKHdHubDownloadProvider,
    Movies4uDownloadProvider,
    RogMoviesDownloadProvider,
    MultiMoviesDownloadProvider,
    UhdMoviesDownloadProvider,
    MoviesModDownloadProvider,
    TopMoviesDownloadProvider,
    BollyflixDownloadProvider,
    CineMacityDownloadProvider,
)

ACTIVE: list[Provider] = [
    VegaMoviesDownloadProvider(),
    HdHub4uDownloadProvider(),
    FourKHdHubDownloadProvider(),
    Movies4uDownloadProvider(),
    RogMoviesDownloadProvider(),
    MultiMoviesDownloadProvider(),
    UhdMoviesDownloadProvider(),
    MoviesModDownloadProvider(),
    TopMoviesDownloadProvider(),
    BollyflixDownloadProvider(),
    CineMacityDownloadProvider(),
]

DISABLED: list[Provider] = []

_registry: list[Provider] = []
_initialised = False


def init_download_providers() -> None:
    global _initialised
    if _initialised:
        return
    _registry.extend(ACTIVE)
    _initialised = True


def get_download_providers() -> list[Provider]:
    return list(_registry)


def get_download_providers_for_kind(kind: str) -> list[Provider]:
    return [p for p in _registry if p.serves_kind(kind)]  # type: ignore[arg-type]
