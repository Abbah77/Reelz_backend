"""
USERS/models.py — Database models.

Tables:
  users        — one row per Google account
  sessions     — JWT refresh token tracking (optional; soft-logout possible)
  watchlist    — user's saved media IDs
  history      — watch progress per media/episode
  payments     — Paystack transaction log
"""
from __future__ import annotations

import time
from typing import Optional

from sqlalchemy import (
    Boolean, Float, Index, Integer, String, Text, BigInteger,
    ForeignKey, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from USERS.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str]         = mapped_column(String(64),  primary_key=True)  # our UUID
    google_sub: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    email: Mapped[str]      = mapped_column(String(256), unique=True, index=True)
    name: Mapped[str]       = mapped_column(String(256), default="")
    photo_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Premium status
    is_premium: Mapped[bool]      = mapped_column(Boolean, default=False)
    plan: Mapped[str]             = mapped_column(String(32), default="none")  # none | monthly | yearly
    premium_expires_at: Mapped[int] = mapped_column(BigInteger, default=0)    # unix ms

    created_at: Mapped[int] = mapped_column(BigInteger, default=lambda: int(time.time() * 1000))
    updated_at: Mapped[int] = mapped_column(BigInteger, default=lambda: int(time.time() * 1000),
                                            onupdate=lambda: int(time.time() * 1000))

    # Relationships
    watchlist: Mapped[list["WatchlistItem"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    history:   Mapped[list["WatchHistory"]]  = relationship(back_populates="user", cascade="all, delete-orphan")
    payments:  Mapped[list["Payment"]]        = relationship(back_populates="user", cascade="all, delete-orphan")

    def is_premium_active(self) -> bool:
        return self.is_premium and (self.premium_expires_at == 0 or self.premium_expires_at > int(time.time() * 1000))

    @property
    def status(self) -> str:
        if not self.is_premium:
            return "none"
        if self.premium_expires_at and self.premium_expires_at < int(time.time() * 1000):
            return "expired"
        return self.plan or "active"


class WatchlistItem(Base):
    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("user_id", "media_id"),)

    id:       Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id:  Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[str] = mapped_column(String(64), index=True)   # e.g. "movie:550"
    added_at: Mapped[int] = mapped_column(BigInteger, default=lambda: int(time.time() * 1000))

    user: Mapped["User"] = relationship(back_populates="watchlist")


class WatchHistory(Base):
    __tablename__ = "history"
    __table_args__ = (
        UniqueConstraint("user_id", "media_id", "season", "episode"),
        Index("ix_history_user_updated", "user_id", "watched_at"),
    )

    id:          Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id:     Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    media_id:    Mapped[str] = mapped_column(String(64))
    season:      Mapped[int] = mapped_column(Integer, default=0)
    episode:     Mapped[int] = mapped_column(Integer, default=0)
    position_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    duration_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    watched_at:  Mapped[int] = mapped_column(BigInteger, default=lambda: int(time.time() * 1000))

    user: Mapped["User"] = relationship(back_populates="history")


class Payment(Base):
    __tablename__ = "payments"

    id:         Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id:    Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    reference:  Mapped[str] = mapped_column(String(128), unique=True, index=True)
    plan:       Mapped[str] = mapped_column(String(32))         # monthly | yearly
    amount:     Mapped[int] = mapped_column(Integer, default=0) # in kobo
    currency:   Mapped[str] = mapped_column(String(8), default="NGN")
    status:     Mapped[str] = mapped_column(String(32), default="pending")  # pending | success | failed
    created_at: Mapped[int] = mapped_column(BigInteger, default=lambda: int(time.time() * 1000))
    verified_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    user: Mapped["User"] = relationship(back_populates="payments")
