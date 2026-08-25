"""
api/user_auth.py — User authentication routes.

POST /auth/google   — exchange Google ID token for our JWT
POST /auth/refresh  — refresh access token using refresh_token
POST /auth/sync     — sync watch history (login required)

Schema v4 notes:
- name, email, photo_url are NOT returned by /auth/google or /auth/refresh
  (Google SDK gives these to the app directly on sign-in)
- /auth/refresh uses Authorization: Bearer <refresh_token>
- /auth/sync only syncs history — watchlist is 100% local (Room DB)

All responses wrapped in the standard envelope:
  { "ok": true, "data": {...}, "error": null, "cache_ttl_ms": null }
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from USERS.db import get_db
from USERS.jwt import verify as verify_jwt, require_user
from api.envelope import ok

router = APIRouter(prefix="/auth", tags=["Auth"])


# ── Request bodies ─────────────────────────────────────────────────────────────

class GoogleAuthBody(BaseModel):
    id_token: str = Field(..., description="Google ID token")


class HistorySyncItem(BaseModel):
    id:          str = ""
    season:      int = 0
    episode:     int = 0
    position_ms: int = 0
    duration_ms: int = 0
    watched_at:  int = 0


class SyncBody(BaseModel):
    history: list[HistorySyncItem] = Field(default_factory=list)


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/google")
async def auth_google(
    body: GoogleAuthBody,
    db:   AsyncSession = Depends(get_db),
):
    """
    Exchange a Google ID token for Reelz access + refresh tokens.
    name, email, photo_url are NOT returned — Google SDK provides them.

    Response data: { user_id, access_token, refresh_token, expires_at_ms,
                     premium, premium_expires_at_ms }
    """
    from USERS.google_auth import google_sign_in
    result = await google_sign_in(body.id_token, db)

    # google_sign_in returns the flat dict including ok=True — strip ok and wrap
    result.pop("ok", None)
    return ok(result, cache_ttl_ms=None)


@router.post("/refresh")
async def refresh_session(
    authorization: str = Header(..., description="Bearer <refresh_token>"),
    db: AsyncSession = Depends(get_db),
):
    """
    Use refresh_token to get a new access_token.
    Header: Authorization: Bearer <refresh_token>

    Response data: { access_token, expires_at_ms }
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")

    token   = authorization.removeprefix("Bearer ").strip()
    payload = verify_jwt(token)   # raises 401 if invalid/expired
    user_id = payload["sub"]

    from sqlalchemy import select
    from USERS.models import User
    from USERS.jwt import sign

    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    new_token, expires_at_ms = sign(user.id, user.email)

    return ok(
        {
            "access_token":  new_token,
            "expires_at_ms": expires_at_ms,
        },
        cache_ttl_ms=None,
    )


@router.post("/sync")
async def sync_user_data(
    body:    SyncBody,
    user_id: str = Depends(require_user),
    db:      AsyncSession = Depends(get_db),
):
    """
    Sync watch history to the server for cross-device continuity.
    Watchlist is 100% local (Room DB) — not synced here.

    Response data: {} (empty object — simple acknowledgement)
    """
    from USERS.sync import sync_history

    history_dicts = [item.model_dump() for item in body.history]
    await sync_history(
        user_id=user_id,
        history_items=history_dicts,
        db=db,
    )
    return ok({}, cache_ttl_ms=None)
