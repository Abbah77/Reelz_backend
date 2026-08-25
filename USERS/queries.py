"""
USERS/queries.py — Reusable database query helpers.

Business logic that reads User state belongs here, not in API route files.

Usage:
    from USERS.queries import is_premium_user

    premium = await is_premium_user(user_id)
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from USERS.db import SessionLocal
from USERS.models import User


async def is_premium_user(user_id: Optional[str]) -> bool:
    """
    Return True if the user exists and has an active premium subscription.
    Returns False for guests (user_id=None) and on any DB error.
    """
    if not user_id:
        return False
    try:
        async with SessionLocal() as session:
            result = await session.execute(select(User).where(User.id == user_id))
            user: Optional[User] = result.scalar_one_or_none()
        return bool(user and user.is_premium_active())
    except Exception:
        return False
