"""
FastAPI application — entry point.

Performance choices vs the Node version:
  - orjson response class  → JSON serialisation 5-10× faster than stdlib
  - HTTP/2 shared client   → TLS session reuse across provider calls
  - asyncio.gather fan-out → true concurrency, no Promise.allSettled overhead
  - lxml HTML parser       → 3-5× faster than html.parser
  - uvicorn[standard]      → uvloop + httptools (C-level event loop + HTTP parser)
  - Brotli compression     → smaller payloads than gzip

Upgraded in v2.1:
  - Circuit breaker (provider_stats) — dead providers skip for 10-min cooldown
  - SSRF protection (utils/ssrf)     — guards proxy + all caller-supplied URLs
  - Torrent streaming endpoints      — /api/v1/torrent (P2P + debrid)
  - /api/v1/providers                — per-provider health + breaker status
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

import orjson
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.providers import init_providers
from app.routes.api import router
from app.routes.torrent import router as torrent_router

_settings = get_settings()


# ── orjson response ───────────────────────────────────────────────────────────

class ORJSONResponse(JSONResponse):
    media_type = "application/json"

    def render(self, content) -> bytes:
        return orjson.dumps(
            content,
            option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY,
        )


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_providers()
    from app.utils.http import get_client
    await get_client()          # warm the HTTP/2 client
    yield
    # Shutdown
    from app.utils.http import _client
    if _client and not _client.is_closed:
        await _client.aclose()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Reelz Stream Engine",
    version="2.1.0",
    description="High-performance Python streaming backend with circuit breaker, SSRF protection, and torrent streaming",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ──────────────────────────────────────────────────────────────────────

origins = (
    [o.strip() for o in _settings.cors_origins.split(",")]
    if _settings.cors_origins != "*"
    else ["*"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request-timing middleware ─────────────────────────────────────────────────

@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    t0 = time.monotonic()
    response: Response = await call_next(request)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    response.headers["X-Response-Time-Ms"] = str(elapsed_ms)
    return response


# ── Routes ────────────────────────────────────────────────────────────────────

app.include_router(router)
app.include_router(torrent_router)


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    return {
        "name": "Reelz Stream Engine",
        "version": "2.1.0",
        "docs": "/docs",
        "health": "/api/v1/health",
        "providers": "/api/v1/providers",
        "torrent": "/api/v1/torrent/stats",
    }
