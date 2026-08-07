"""
Reelz Stream Engine — entry point.

Performance stack:
  - orjson          → JSON 5-10× faster than stdlib
  - HTTP/2 client   → TLS session reuse across all provider calls
  - asyncio.gather  → true concurrency fan-out
  - lxml parser     → 3-5× faster than html.parser
  - uvicorn[std]    → uvloop + httptools (C-level event loop + HTTP parser)
  - Brotli          → smaller payloads than gzip

Security middleware (ordered — outermost runs first on request):
  1. verify_token   — rejects unauthenticated callers with 403
  2. add_timing     — adds X-Response-Time-Ms header on every response
  3. CORS           — allows configured origins only
  4. slowapi         — rate-limits /streams and /downloads by client IP
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

import orjson
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.limiter import limiter  # defined in its own module to avoid circular imports
from app.config import get_settings
from app.providers.stream.registry import init_stream_providers
from app.providers.download.registry import init_download_providers
from app.providers.subtitle.registry import init_subtitle_providers
from app.api.streams import router as streams_router
from app.api.downloads import router as downloads_router
from app.api.subtitles import router as subtitles_router
from app.api.health import router as health_router
from app.api.payments import router as payments_router

_settings = get_settings()

# Paths that are always publicly accessible — no token, no rate limit.
# Paystack webhook is authenticated by HMAC signature, not by our app token.
_OPEN_PATHS = frozenset({
    "/", "/api/v1/health", "/api/v1/health/providers",
    "/docs", "/redoc", "/openapi.json",
    "/api/v1/payments/webhook",
})


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

# Attach limiter to app state so slowapi can find it via the decorator
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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


# ── Auth middleware — X-Reelz-Token ───────────────────────────────────────────
# Runs on every request. Open paths (health, docs, root) bypass the check.
# If app_secret_token is blank (not configured), auth is effectively disabled
# so the app keeps working during the deployment transition period.

@app.middleware("http")
async def verify_token(request: Request, call_next):
    if request.url.path in _OPEN_PATHS:
        return await call_next(request)

    secret = _settings.app_secret_token
    if secret:
        token = request.headers.get("X-Reelz-Token", "")
        if token != secret:
            return Response(
                content=orjson.dumps({"detail": "Forbidden"}),
                status_code=403,
                media_type="application/json",
            )

    return await call_next(request)


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
app.include_router(payments_router)


@app.get("/", include_in_schema=False)
async def root():
    return {
        "name": "Reelz Stream Engine",
        "version": "3.0.0",
        "docs": "/docs",
        "health": "/api/v1/health",
        "providers": "/api/v1/health/providers",
    }
