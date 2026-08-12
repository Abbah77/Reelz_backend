"""
api/user_auth.py — User authentication routes.

POST /auth/google   — exchange Google ID token for our JWT
POST /auth/refresh  — refresh an existing session
POST /auth/sync     — sync watchlist + watch history

No X-Reelz-Token required on auth routes — they're called before
the app has that token. JWT (Bearer) auth is used instead on refresh + sync.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from USERS.db import get_db
from USERS.jwt import verify as verify_jwt, require_user

router = APIRouter(prefix="/auth", tags=["Auth"])


# ── Request bodies ─────────────────────────────────────────────────────────────

class GoogleAuthBody(BaseModel):
    id_token: str = Field(..., description="Google ID token from Firebase Auth")


class HistorySyncItem(BaseModel):
    id:          str  = ""
    season:      int  = 0
    episode:     int  = 0
    position_ms: int  = 0
    duration_ms: int  = 0
    watched_at:  int  = 0


class SyncBody(BaseModel):
    watchlist: list[str]            = Field(default_factory=list)
    history:   list[HistorySyncItem] = Field(default_factory=list)


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/google")
async def auth_google(
    body: GoogleAuthBody,
    db:   AsyncSession = Depends(get_db),
):
    """
    Exchange a Google ID token for a Reelz JWT.
    Creates the user account on first sign-in.
    """
    from USERS.google_auth import google_sign_in
    return await google_sign_in(body.id_token, db)


@router.post("/refresh")
async def refresh_session(
    authorization: str = Header(..., description="Bearer <access_token>"),
    db: AsyncSession = Depends(get_db),
):
    """
    Validate an existing token and return a fresh one.
    Called by the app when the token is near expiry.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")

    token   = authorization.removeprefix("Bearer ").strip()
    payload = verify_jwt(token)          # raises 401 if invalid/expired
    user_id = payload["sub"]

    from sqlalchemy import select
    from USERS.models import User
    from USERS.jwt import sign

    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    new_token, expires_at_ms = sign(user.id, user.email)

    return {
        "ok":            True,
        "user_id":       user.id,
        "access_token":  new_token,
        "premium":       user.is_premium_active(),
        "status":        user.status,
        "expires_at_ms": expires_at_ms,
        "name":          user.name,
        "email":         user.email,
        "photo_url":     user.photo_url,
    }


@router.post("/sync")
async def sync_user_data(
    body:    SyncBody,
    user_id: str = Depends(require_user),
    db:      AsyncSession = Depends(get_db),
):
    """
    Bidirectional watchlist + history sync.
    Client sends its local state; server merges and returns server state.
    """
    from USERS.sync import sync

    history_dicts = [item.model_dump() for item in body.history]
    return await sync(
        user_id=user_id,
        watchlist_ids=body.watchlist,
        history_items=history_dicts,
        db=db,
    )
