"""
USERS/db.py — Database engine and session factory.

Supports SQLite (dev/small prod) and Postgres (prod).
Set DATABASE_URL in .env:
  SQLite:   sqlite+aiosqlite:///./reelz.db
  Postgres: postgresql+asyncpg://user:pass@host/dbname
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from config import get_settings

_s = get_settings()

engine = create_async_engine(
    _s.database_url,
    echo=_s.debug,
    pool_pre_ping=True,
    # SQLite-specific: needed for WAL mode and concurrent reads
    connect_args={"check_same_thread": False} if "sqlite" in _s.database_url else {},
)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    """Create tables if they don't already exist. Safe to call on every startup."""
    # Import models so they register with Base.metadata
    import USERS.models  # noqa: F401
    async with engine.begin() as conn:
        # checkfirst=True: skip tables that already exist instead of raising.
        # Re-deploys and restarts never crash here.
        # For schema migrations (adding/altering columns) use Alembic.
        await conn.run_sync(lambda c: Base.metadata.create_all(c, checkfirst=True))


async def get_db():
    """FastAPI dependency — yields an AsyncSession and closes it after the request."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
