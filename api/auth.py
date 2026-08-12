"""
api/auth.py — Request authentication for catalog and ENGINE endpoints.

Three tiers of access:
  GUEST        — no credentials, can browse all catalog (feed, discover, search, media, shorts)
  FREE_USER    — valid JWT (logged in), same catalog access + watchlist/history/sync
  PREMIUM_USER — valid JWT + user.is_premium_active(), unlocks premium quality & unlimited downloads

Two dependencies are exposed:

  verify          — PUBLIC catalog routes (feed, discover, genres, search, media, shorts).
                    Accepts: valid JWT, legacy X-Reelz-Token, or NO credentials at all (guests).
                    Returns an optional user_id (None for guests).

  verify_engine   — ENGINE routes that require a logged-in user (stream, subtitle).
                    Requires a valid JWT. Returns user_id.
                    Raises 401 if unauthenticated.

  verify_premium  — ENGINE routes restricted to premium users (HD quality gate, unlimited downloads).
                    Requires a valid JWT AND is_premium_active(). Returns user_id.
                    Raises 401 if unauthenticated, 403 if not premium.

Usage:
    from api.auth import verify, verify_engine, verify_premium
    @router.get("/feed")                   # guest-ok
    async def feed(_=Depends(verify)): ...

    @router.post("/stream")                # login required
    async def stream(uid=Depends(verify_engine)): ...

    @router.post("/download/hd")           # premium required
    async def hd(uid=Depends(verify_premium)): ...
"""
from __future__ import annotations

from typing import Optional

import jwt as pyjwt
from fastapi import Header, HTTPException, Request
from config import get_settings

_s = get_settings()


def _jwt_user_id(authorization: str) -> Optional[str]:
    """Return user_id from a 'Bearer <token>' header, or None if invalid/absent."""
    if not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = pyjwt.decode(token, _s.jwt_secret, algorithms=[_s.jwt_algorithm])
        return payload.get("sub")
    except Exception:
        return None


async def verify(
    request: Request,
    authorization: str = Header(default=""),
    x_reelz_token: str = Header(default=""),
) -> Optional[str]:
    """
    PUBLIC catalog-route guard — guests are welcome.

    Resolution order:
      1. Valid JWT Bearer token  → returns user_id (FREE_USER or PREMIUM_USER)
      2. Legacy X-Reelz-Token   → returns None (treated as authenticated guest)
      3. No credentials          → returns None (GUEST, always allowed)

    No 403 is ever raised here; this dependency is permissive by design.
    Use verify_engine / verify_premium for routes that need identity.
    """
    # Try JWT first (logged-in users get their user_id propagated)
    uid = _jwt_user_id(authorization)
    if uid:
        return uid

    # Legacy static token from old APK builds — still let them through
    if _s.app_secret_token and x_reelz_token == _s.app_secret_token:
        return None

    # No credentials → guest, always allowed
    return None


async def verify_engine(
    request: Request,
    authorization: str = Header(default=""),
) -> str:
    """
    ENGINE-route guard — requires a logged-in user (FREE_USER or PREMIUM_USER).

    Returns user_id on success.
    Raises 401 if token is absent or invalid.
    """
    uid = _jwt_user_id(authorization)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    return uid


async def verify_premium(
    request: Request,
    authorization: str = Header(default=""),
) -> str:
    """
    Premium-route guard — requires a valid JWT AND an active premium subscription.

    Returns user_id on success.
    Raises 401 if unauthenticated, 403 if the account is not premium.
    """
    uid = _jwt_user_id(authorization)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Lazy import to avoid circular dependency at module load time
    from USERS.db import SessionLocal
    from USERS.models import User
    from sqlalchemy import select

    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.id == uid))
        user: Optional[User] = result.scalar_one_or_none()

    if not user or not user.is_premium_active():
        raise HTTPException(
            status_code=403,
            detail="Premium subscription required",
        )

    return uid
