"""
ENGINE/providers/Shorts/registry.py — Shorts provider registry.

THE ONLY FILE THAT KNOWS SHORTS PROVIDERS EXIST.

Provider ID range: R-301 to R-399
"""
from __future__ import annotations

from ENGINE.providers.base import Provider
from ENGINE.providers.Shorts.R_301.R_301 import R301Provider

ACTIVE: list[Provider] = [
    R301Provider(),   # TMDB Trailers
]

DISABLED: list[Provider] = []

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
