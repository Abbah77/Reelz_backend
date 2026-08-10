"""
main.py — Reelz application entry point.

Starts FastAPI and mounts the root api/ gate.
Run: uvicorn main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from api.stream import router as stream_router
from api.download import router as download_router
from api.subtitle import router as subtitle_router
from api.shorts import router as shorts_router
from api.health import router as health_router

_s = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Boot ENGINE provider registries at startup
    from ENGINE.providers.Stream.registry import init as init_stream
    from ENGINE.providers.Download.registry import init as init_download
    from ENGINE.providers.Subtitle.registry import init as init_subtitle
    from ENGINE.providers.Shorts.registry import init as init_shorts
    init_stream()
    init_download()
    init_subtitle()
    init_shorts()
    yield


app = FastAPI(
    title="Reelz",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

# CORS
origins = [o.strip() for o in _s.cors_origins.split(",")] if _s.cors_origins != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount gate routes
app.include_router(health_router)
app.include_router(stream_router)
app.include_router(download_router)
app.include_router(subtitle_router)
app.include_router(shorts_router)


@app.get("/", include_in_schema=False)
async def root():
    return {"name": "Reelz", "version": "1.0.0"}
