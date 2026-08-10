"""
ENGINE/providers/Download/registry.py — Download provider registry.

THE ONLY FILE THAT KNOWS DOWNLOAD PROVIDERS EXIST.

Add: create R-XXX folder + import here + one line in ACTIVE
Remove: delete folder + remove one line here
Disable: move to DISABLED

Provider ID range: R-101 to R-199
"""
from __future__ import annotations

from ENGINE.providers.base import Provider
from ENGINE.providers.Download.R_101.R_101 import R101Provider

ACTIVE: list[Provider] = [
    R101Provider(),
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
