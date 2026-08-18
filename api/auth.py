"""
api/auth.py — Request authentication for catalog and ENGINE endpoints.

Per schema v3 access matrix:
  GUEST      — unauthenticated, identical access to signed-in free user
  FREE_USER  — valid JWT, adds watch history logging
  PREMIUM    — valid JWT + active premium

Dependencies exposed:

  verify          — PUBLIC routes (feed, discover, genres, search, media, shorts,
                    stream, download, subtitles).
                    Accepts JWT, or NO credentials (guests).
                    Returns optional user_id. Never raises 401.

  verify_strict   — Routes that strictly require login (sync, refresh, payment).
                    Raises 401 if unauthenticated.
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
) -> Optional[str]:
    """
    PUBLIC route guard — guests are always welcome.

    Returns user_id if a valid JWT is present, None otherwise.
    Never raises 401 — guests get identical service to free users.
    """
    return _jwt_user_id(authorization)


async def verify_engine(
    request: Request,
    authorization: str = Header(default=""),
) -> str:
    """
    Strict guard — requires a logged-in user.
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
    Premium guard — requires valid JWT AND active premium.
    Raises 401 if unauthenticated, 403 if not premium.
    """
    uid = _jwt_user_id(authorization)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")

    from USERS.db import SessionLocal
    from USERS.models import User
    from sqlalchemy import select

    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.id == uid))
        user: Optional[User] = result.scalar_one_or_none()

    if not user or not user.is_premium_active():
        raise HTTPException(status_code=403, detail="Premium subscription required")

    return uid
