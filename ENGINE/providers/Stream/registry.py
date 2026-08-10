"""
ENGINE/providers/Stream/registry.py — Stream provider registry.

THE ONLY FILE THAT KNOWS STREAM PROVIDERS EXIST.

Add a provider:
    1. Create ENGINE/providers/Stream/R-XXX/ folder
    2. Create R-XXX.py inside with a Provider subclass
    3. Import it below + add one line to ACTIVE

Remove a provider:
    - Delete the folder + remove one line here

Disable temporarily:
    - Move from ACTIVE to DISABLED list

Provider ID range: R-001 to R-099
"""
from __future__ import annotations

from ENGINE.providers.base import Provider

# ── Imports ───────────────────────────────────────────────────────────────────

from ENGINE.providers.Stream.R_001.R_001 import R001Provider   # 2Embed
from ENGINE.providers.Stream.R_002.R_002 import R002Provider   # VidFast
from ENGINE.providers.Stream.R_003.R_003 import R003Provider   # VidRock
from ENGINE.providers.Stream.R_004.R_004 import R004Provider   # HexaSU
from ENGINE.providers.Stream.R_005.R_005 import R005Provider   # AllMovieLand
from ENGINE.providers.Stream.R_006.R_006 import R006Provider   # Xpass
from ENGINE.providers.Stream.R_007.R_007 import R007Provider   # VaplayerV2
from ENGINE.providers.Stream.R_008.R_008 import R008Provider   # DahmerMovies
from ENGINE.providers.Stream.R_009.R_009 import R009Provider   # RiveStream
from ENGINE.providers.Stream.R_010.R_010 import R010Provider   # PrimeVids
from ENGINE.providers.Stream.R_011.R_011 import R011Provider   # KissKh
from ENGINE.providers.Stream.R_012.R_012 import R012Provider   # Castle
from ENGINE.providers.Stream.R_013.R_013 import R013Provider   # HDRezka
from ENGINE.providers.Stream.R_014.R_014 import R014Provider   # AniZone
from ENGINE.providers.Stream.R_015.R_015 import R015Provider   # AniNeko
from ENGINE.providers.Stream.R_016.R_016 import R016Provider   # AnimeNoSub
from ENGINE.providers.Stream.R_017.R_017 import R017Provider   # AnimeWorld
from ENGINE.providers.Stream.R_018.R_018 import R018Provider   # VegaMovies
from ENGINE.providers.Stream.R_019.R_019 import R019Provider   # HdHub4u
from ENGINE.providers.Stream.R_020.R_020 import R020Provider   # 4KHdHub
from ENGINE.providers.Stream.R_021.R_021 import R021Provider   # Movies4u
from ENGINE.providers.Stream.R_022.R_022 import R022Provider   # RogMovies
from ENGINE.providers.Stream.R_023.R_023 import R023Provider   # MultiMovies
from ENGINE.providers.Stream.R_024.R_024 import R024Provider   # UhdMovies
from ENGINE.providers.Stream.R_025.R_025 import R025Provider   # Moviesmod

# ── ACTIVE — priority order (fastest/most reliable first) ────────────────────

ACTIVE: list[Provider] = [
    R001Provider(),
    R002Provider(),
    R003Provider(),
    R004Provider(),
    R005Provider(),
    R006Provider(),
    R007Provider(),
    R008Provider(),
    R009Provider(),
    R010Provider(),
    R011Provider(),
    R012Provider(),
    R013Provider(),
    R014Provider(),
    R015Provider(),
    R016Provider(),
    R017Provider(),
    R018Provider(),
    R019Provider(),
    R020Provider(),
    R021Provider(),
    R022Provider(),
    R023Provider(),
    R024Provider(),
    R025Provider(),
]

# ── DISABLED — comment out or move here to pause a provider ──────────────────

DISABLED: list[Provider] = []

# ── Internal registry ─────────────────────────────────────────────────────────

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
