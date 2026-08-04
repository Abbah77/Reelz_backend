"""
providers/subtitle/registry.py — subtitle provider registry.

THE ONLY FILE that knows subtitle providers exist.
Add: one import + one line in ACTIVE.
Remove: delete folder + remove one line.
"""
from __future__ import annotations

from app.providers.base import Provider
from app.providers.subtitle.opensubtitles.provider import OpenSubtitlesProvider
from app.providers.subtitle.wyzie.provider import WyzieSubsProvider

ACTIVE: list[Provider] = [
    OpenSubtitlesProvider(),
    WyzieSubsProvider(),
]

DISABLED: list[Provider] = []

_registry: list[Provider] = []
_initialised = False


def init_subtitle_providers() -> None:
    global _initialised
    if _initialised:
        return
    _registry.extend(ACTIVE)
    _initialised = True


def get_subtitle_providers() -> list[Provider]:
    return list(_registry)
