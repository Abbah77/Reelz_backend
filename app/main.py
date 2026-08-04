"""
Reelz Stream Engine — entry point.

Performance stack:
  - orjson          → JSON 5-10× faster than stdlib
  - HTTP/2 client   → TLS session reuse across all provider calls
  - asyncio.gather  → true concurrency fan-out
  - lxml parser     → 3-5× faster than html.parser
  - uvicorn[std]    → uvloop + httptools (C-level event loop + HTTP parser)
  - Brotli          → smaller payloads than gzip
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

import orjson
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.providers.stream.registry import init_stream_providers
from app.providers.download.registry import init_download_providers
from app.providers.subtitle.registry import init_subtitle_providers
from app.api.streams import router as streams_router
from app.api.downloads import router as downloads_router
from app.api.subtitles import router as subtitles_router
from app.api.health import router as health_router

_settings = get_settings()


# ── orjson response ────────────────────────────────────────────────────────────

class ORJSONResponse(JSONResponse):
    media_type = "application/json"

    def render(self, content) -> bytes:
        return orjson.dumps(
            content,
            option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY,
        )


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(application: FastAPI):
    # Startup — register all providers, warm HTTP client
    init_stream_providers()
    init_download_providers()
    init_subtitle_providers()

    from app.clients.http import get_client
    await get_client()

    yield

    # Shutdown — drain HTTP client
    from app.clients.http import _client
    if _client and not _client.is_closed:
        await _client.aclose()


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Reelz Stream Engine",
    version="3.0.0",
    description="High-performance streaming backend — clean architecture edition",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ───────────────────────────────────────────────────────────────────────

_origins = (
    [o.strip() for o in _settings.cors_origins.split(",")]
    if _settings.cors_origins != "*"
    else ["*"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Timing middleware ──────────────────────────────────────────────────────────

@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    t0 = time.monotonic()
    response: Response = await call_next(request)
    response.headers["X-Response-Time-Ms"] = str(int((time.monotonic() - t0) * 1000))
    return response


# ── Routers ────────────────────────────────────────────────────────────────────

app.include_router(health_router)
app.include_router(streams_router)
app.include_router(downloads_router)
app.include_router(subtitles_router)


@app.get("/", include_in_schema=False)
async def root():
    return {
        "name": "Reelz Stream Engine",
        "version": "3.0.0",
        "docs": "/docs",
        "health": "/api/v1/health",
        "providers": "/api/v1/health/providers",
    }
