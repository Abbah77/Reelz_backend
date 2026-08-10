"""
ENGINE/providers/Subtitle/registry.py — Subtitle provider registry.

THE ONLY FILE THAT KNOWS SUBTITLE PROVIDERS EXIST.

Provider ID range: R-201 to R-299
"""
from __future__ import annotations

from ENGINE.providers.base import Provider
from ENGINE.providers.Subtitle.R_201.R_201 import R201Provider
from ENGINE.providers.Subtitle.R_202.R_202 import R202Provider

ACTIVE: list[Provider] = [
    R201Provider(),   # OpenSubtitles — largest database
    R202Provider(),   # Wyzie — fast fallback
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
