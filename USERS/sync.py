"""
USERS/sync.py — Watchlist and watch-history sync.

The app POSTs its local state; the server merges it and returns
the full server-side state so the app can replace its local DB.

Strategy: last-write-wins by watched_at timestamp.
"""
from __future__ import annotations

import time
from typing import Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from USERS.models import User, WatchlistItem, WatchHistory


async def sync(
    user_id: str,
    watchlist_ids: list[str],
    history_items: list[dict],
    db: AsyncSession,
) -> dict:
    """
    Merge client state into the server DB and return server state.

    watchlist_ids: list of media IDs the client has bookmarked
    history_items: [{id, season, episode, position_ms, duration_ms, watched_at}]
    """
    # ── Watchlist sync ────────────────────────────────────────────────────────
    # Fetch current server watchlist
    result = await db.execute(
        select(WatchlistItem).where(WatchlistItem.user_id == user_id)
    )
    server_wl = {row.media_id: row for row in result.scalars().all()}
    server_ids = set(server_wl.keys())
    client_ids = set(watchlist_ids)

    # Add items the client has but server doesn't
    for mid in client_ids - server_ids:
        db.add(WatchlistItem(user_id=user_id, media_id=mid))

    # Remove items the server has but client doesn't
    for mid in server_ids - client_ids:
        await db.execute(
            delete(WatchlistItem).where(
                WatchlistItem.user_id == user_id,
                WatchlistItem.media_id == mid,
            )
        )

    # ── History sync ──────────────────────────────────────────────────────────
    for item in history_items:
        media_id    = item.get("id", "")
        season      = item.get("season", 0)
        episode     = item.get("episode", 0)
        position_ms = item.get("position_ms", 0)
        duration_ms = item.get("duration_ms", 0)
        watched_at  = item.get("watched_at", int(time.time() * 1000))

        if not media_id:
            continue

        result2 = await db.execute(
            select(WatchHistory).where(
                WatchHistory.user_id  == user_id,
                WatchHistory.media_id == media_id,
                WatchHistory.season   == season,
                WatchHistory.episode  == episode,
            )
        )
        existing = result2.scalar_one_or_none()

        if existing is None:
            db.add(WatchHistory(
                user_id=user_id, media_id=media_id,
                season=season, episode=episode,
                position_ms=position_ms, duration_ms=duration_ms,
                watched_at=watched_at,
            ))
        elif watched_at > existing.watched_at:
            # Client has newer data — update
            existing.position_ms = position_ms
            existing.duration_ms = duration_ms
            existing.watched_at  = watched_at

    await db.flush()

    # ── Return server state ───────────────────────────────────────────────────
    wl_result = await db.execute(
        select(WatchlistItem).where(WatchlistItem.user_id == user_id)
    )
    server_watchlist_ids = [row.media_id for row in wl_result.scalars().all()]

    hist_result = await db.execute(
        select(WatchHistory)
        .where(WatchHistory.user_id == user_id)
        .order_by(WatchHistory.watched_at.desc())
        .limit(200)
    )
    server_history = [
        {
            "id":          row.media_id,
            "season":      row.season,
            "episode":     row.episode,
            "position_ms": row.position_ms,
            "duration_ms": row.duration_ms,
            "watched_at":  row.watched_at,
        }
        for row in hist_result.scalars().all()
    ]

    # For watchlist we need the MediaDto shape — return IDs only here,
    # the client fetches detail cards it doesn't already have cached.
    watchlist_dtos = [{"id": mid} for mid in server_watchlist_ids]

    return {
        "ok":       True,
        "watchlist": watchlist_dtos,
        "history":  server_history,
    }
