"""
api/auth.py — Request authentication for catalog and ENGINE endpoints.

Now that the app is fully server-side (no client-side TMDB calls), the old
static X-Reelz-Token handshake is no longer needed. Authentication is now
handled by the JWT issued after Google sign-in.

Two dependencies are exposed:

  verify          — used on catalog routes (feed, discover, genres, search,
                    media). Accepts any valid JWT (Bearer token). If
                    APP_SECRET_TOKEN is still set it is also accepted as a
                    fallback so old APK builds keep working during rollout.

  verify_engine   — used on ENGINE routes (stream, download, subtitle, shorts).
                    Requires a valid JWT; no fallback to static token.

Usage:
    from api.auth import verify, verify_engine
    @router.get("/feed", dependencies=[Depends(verify)])
    @router.post("/stream", dependencies=[Depends(verify_engine)])
"""
from __future__ import annotations

import jwt as pyjwt
from fastapi import Header, HTTPException, Request
from config import get_settings

_s = get_settings()


def _jwt_user_id(authorization: str) -> str | None:
    """Return user_id from a 'Bearer <token>' header, or None if invalid."""
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
) -> None:
    """
    Catalog-route guard.

    Accepts:
      1. A valid JWT in the Authorization: Bearer <token> header  (new builds)
      2. The static APP_SECRET_TOKEN in X-Reelz-Token             (old builds, fallback)
      3. No auth at all if APP_SECRET_TOKEN is empty              (dev / open mode)

    Raises 403 when a secret is configured but neither credential matches.
    """
    # ── Dev / open mode ───────────────────────────────────────────────────────
    if not _s.app_secret_token and not _s.jwt_secret:
        return

    # ── Valid JWT → allow ─────────────────────────────────────────────────────
    if _jwt_user_id(authorization):
        return

    # ── Legacy static token fallback (old APK builds) ─────────────────────────
    if _s.app_secret_token and x_reelz_token == _s.app_secret_token:
        return

    # ── No valid credential ───────────────────────────────────────────────────
    raise HTTPException(status_code=403, detail="Forbidden")


async def verify_engine(
    request: Request,
    authorization: str = Header(default=""),
) -> str:
    """
    ENGINE-route guard (stream, download, subtitle, shorts).

    Requires a valid JWT. Returns the user_id so routes can use it
    for premium checks, rate limiting, etc.

    Raises 401 if the token is missing or invalid.
    """
    uid = _jwt_user_id(authorization)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    return uid
