"""
USERS/sync.py — Watch-history sync.

Per schema v3: Watchlist is 100% local (Room DB). Only history is synced.
Strategy: last-write-wins by watched_at timestamp.
"""
from __future__ import annotations

import time
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from USERS.models import WatchHistory


async def sync_history(
    user_id: str,
    history_items: list[dict],
    db: AsyncSession,
) -> None:
    """
    Merge client watch history into the server DB.
    history_items: [{id, season, episode, position_ms, duration_ms, watched_at}]
    """
    for item in history_items:
        media_id    = item.get("id", "")
        season      = item.get("season", 0)
        episode     = item.get("episode", 0)
        position_ms = item.get("position_ms", 0)
        duration_ms = item.get("duration_ms", 0)
        watched_at  = item.get("watched_at", int(time.time() * 1000))

        if not media_id:
            continue

        result = await db.execute(
            select(WatchHistory).where(
                WatchHistory.user_id  == user_id,
                WatchHistory.media_id == media_id,
                WatchHistory.season   == season,
                WatchHistory.episode  == episode,
            )
        )
        existing = result.scalar_one_or_none()

        if existing is None:
            db.add(WatchHistory(
                user_id=user_id, media_id=media_id,
                season=season, episode=episode,
                position_ms=position_ms, duration_ms=duration_ms,
                watched_at=watched_at,
            ))
        elif watched_at > existing.watched_at:
            existing.position_ms = position_ms
            existing.duration_ms = duration_ms
            existing.watched_at  = watched_at

    await db.flush()
