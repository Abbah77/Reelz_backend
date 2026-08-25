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

JWT decoding is handled exclusively by USERS/jwt.py — this file never
calls pyjwt directly.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException, Request

from USERS.jwt import user_id_from_request, require_user as _require_user


async def verify(
    request: Request,
    authorization: str = Header(default=""),
) -> Optional[str]:
    """
    PUBLIC route guard — guests are always welcome.

    Returns user_id if a valid JWT is present, None otherwise.
    Never raises 401 — guests get identical service to free users.
    """
    return user_id_from_request(request)


async def verify_strict(
    request: Request,
    authorization: str = Header(default=""),
) -> str:
    """
    Strict guard — requires a logged-in user.
    Raises 401 if token is absent or invalid.
    Delegates to USERS/jwt.require_user.
    """
    return await _require_user(request)
