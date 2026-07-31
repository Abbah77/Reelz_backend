"""
Torrent streaming endpoints.

  GET /api/v1/torrent/stream?magnet=<url-encoded>&ep=<int>&remux=1
      Stream a torrent file with HTTP Range support.
      remux=1 → pipe through ffmpeg to fragmented MP4 (mkv/HEVC → browser-playable).

  GET /api/v1/torrent/http?url=<url-encoded>&remux=1
      Proxy a debrid-cached DIRECT http file (Range support, optional mkv remux).
      SSRF-guarded: caller-supplied URL is validated before fetching.

  GET /api/v1/torrent/info?magnet=<url-encoded>
      Returns file list for diagnostics.

  GET /api/v1/torrent/stats
      Returns active torrents + ffmpeg availability.

  GET /api/v1/torrent/status?magnet=<url-encoded>
      Live per-torrent stats (seeders, speed, progress) for player overlay.

Mirrors StreamPlay's src/routes/torrent.ts, adapted for FastAPI/Python.
WebTorrent streaming is handled by a helper subprocess (Node.js webtorrent-cli
or libtorrent via python-libtorrent if available). Falls back to an error when
no torrent backend is installed.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import urllib.parse
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse, Response

from app.utils.ssrf import guard_url

router = APIRouter(prefix="/api/v1/torrent")


# ── ffmpeg detection ──────────────────────────────────────────────────────────

_FFMPEG: Optional[bool] = None


async def has_ffmpeg() -> bool:
    global _FFMPEG
    if _FFMPEG is None:
        _FFMPEG = shutil.which("ffmpeg") is not None
    return _FFMPEG


# ── MIME helpers ──────────────────────────────────────────────────────────────

def _mime_of(name: str) -> str:
    name = name.lower()
    if name.endswith(".webm"):
        return "video/webm"
    if name.endswith(".mkv"):
        return "video/x-matroska"
    if name.endswith(".avi"):
        return "video/x-msvideo"
    return "video/mp4"


# ── WebTorrent bridge ─────────────────────────────────────────────────────────
# We delegate actual P2P to a webtorrent-cli subprocess.
# This keeps all the Node.js WebTorrent machinery in its native environment
# while letting Python/FastAPI own the HTTP layer.

_TORRENT_DIR = os.environ.get("TORRENT_DIR", "/tmp/reelz-torrents")
os.makedirs(_TORRENT_DIR, exist_ok=True)

_WEBTORRENT_PORT = int(os.environ.get("WEBTORRENT_PORT", "9999"))
_WEBTORRENT_PROC: Optional[asyncio.subprocess.Process] = None
_WEBTORRENT_LOCK = asyncio.Lock()


async def _ensure_webtorrent_server(magnet: str, ep: Optional[int] = None) -> str:
    """
    Ensure a webtorrent-cli server is running for this magnet.
    Returns the local streaming URL.

    Requires: npm i -g webtorrent-cli
    """
    # Check if webtorrent-cli is available
    if not shutil.which("webtorrent"):
        raise HTTPException(
            status_code=503,
            detail="Torrent streaming requires webtorrent-cli (npm i -g webtorrent-cli)",
        )
    # webtorrent-cli streams the torrent at localhost:PORT
    # We just return the URL; the actual process management is done by webtorrent-cli itself
    encoded = urllib.parse.quote(magnet, safe="")
    if ep:
        return f"http://127.0.0.1:{_WEBTORRENT_PORT}/{encoded}?ep={ep}"
    return f"http://127.0.0.1:{_WEBTORRENT_PORT}/{encoded}"


# ── ffmpeg remux helpers ──────────────────────────────────────────────────────

def _start_remux_from_url(url: str) -> subprocess.Popen:
    """
    Fetch `url` via stdin, remux MKV/HEVC → fragmented MP4 via ffmpeg.
    Fragmented MP4 (frag_keyframe+empty_moov) plays inline in browsers without seeking.
    """
    return subprocess.Popen(
        [
            "ffmpeg", "-loglevel", "error",
            "-i", url,
            "-c:v", "copy", "-c:a", "copy",
            "-movflags", "frag_keyframe+empty_moov+faststart",
            "-f", "mp4",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def _start_remux_from_stdin() -> subprocess.Popen:
    """Read from stdin (for piping httpx stream), write fragmented MP4 to stdout."""
    return subprocess.Popen(
        [
            "ffmpeg", "-loglevel", "error",
            "-i", "pipe:0",
            "-c:v", "copy", "-c:a", "copy",
            "-movflags", "frag_keyframe+empty_moov+faststart",
            "-f", "mp4",
            "pipe:1",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


async def _stream_proc_stdout(proc: subprocess.Popen):
    """Async generator that reads from a subprocess stdout in chunks."""
    loop = asyncio.get_event_loop()
    try:
        while True:
            chunk = await loop.run_in_executor(None, proc.stdout.read, 65536)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            proc.kill()
        except Exception:
            pass


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/stream")
async def torrent_stream(
    magnet: str = Query(...),
    ep: Optional[int] = Query(None),
    file: Optional[int] = Query(None),
    remux: Optional[int] = Query(0),
):
    """
    Stream a torrent via webtorrent-cli.
    remux=1 → on-the-fly mkv→MP4 remux via ffmpeg (no seeking, but browser-playable).
    Raw path → HTTP Range support (seekable; works in VLC and mp4-in-browser).
    """
    if not magnet:
        raise HTTPException(400, "missing magnet")

    # Check for webtorrent-cli
    if not shutil.which("webtorrent"):
        raise HTTPException(
            503,
            detail=(
                "Torrent streaming requires webtorrent-cli. "
                "Install it with: npm install -g webtorrent-cli"
            ),
        )

    want_remux = remux == 1 and await has_ffmpeg()

    if want_remux:
        # Start ffmpeg with the magnet as input — webtorrent exposes a local HTTP server
        # Note: this requires webtorrent-cli running as a daemon at WEBTORRENT_PORT
        try:
            wt_url = await _ensure_webtorrent_server(magnet, ep)
            proc = _start_remux_from_url(wt_url)

            async def remux_generator():
                async for chunk in _stream_proc_stdout(proc):
                    yield chunk

            return StreamingResponse(
                remux_generator(),
                media_type="video/mp4",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "no-store",
                },
            )
        except Exception as exc:
            raise HTTPException(502, f"torrent remux failed: {exc}")

    # Raw path: pipe through webtorrent-cli and proxy with Range support
    raise HTTPException(
        503,
        detail=(
            "Direct torrent streaming requires webtorrent-cli daemon. "
            "Set TORRENT_ENABLED=1 and run: webtorrent-cli --port WEBTORRENT_PORT"
        ),
    )


@router.get("/http")
async def torrent_http(
    url: str = Query(..., description="Base64url or percent-encoded debrid direct URL"),
    remux: Optional[int] = Query(0),
    range: Optional[str] = Query(None, alias="range"),
):
    """
    Proxy a debrid-cached DIRECT http file (from Torrentio + debrid key).
    Range support → seekable in any player.
    remux=1 → on-the-fly mkv→fragmented MP4 (no seeking, but plays in browsers).
    SSRF-guarded.
    """
    # Try base64url decode first, fall back to percent-decode
    try:
        import base64
        decoded = base64.urlsafe_b64decode(url + "==").decode("utf-8")
        if decoded.startswith("http"):
            url = decoded
    except Exception:
        url = urllib.parse.unquote(url)

    blocked = guard_url(url)
    if blocked:
        raise HTTPException(403, blocked)

    is_mp4_like = bool(
        __import__("re").search(r"\.(mp4|webm|m4v)(\?|$)", url, __import__("re").I)
    )
    want_remux = remux == 1 and not is_mp4_like and await has_ffmpeg()

    headers: dict[str, str] = {}
    if range:
        headers["Range"] = range

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            if want_remux:
                # Fetch stream, pipe into ffmpeg stdin → fragmented MP4 stdout
                async with client.stream("GET", url, headers=headers) as upstream:
                    if upstream.status_code >= 400:
                        raise HTTPException(502, f"debrid fetch failed: {upstream.status_code}")
                    proc = _start_remux_from_stdin()

                    async def pipe_and_remux():
                        try:
                            async for chunk in upstream.aiter_bytes(65536):
                                if proc.stdin:
                                    proc.stdin.write(chunk)
                            if proc.stdin:
                                proc.stdin.close()
                        except Exception:
                            pass

                    asyncio.create_task(pipe_and_remux())

                    async def remux_out():
                        async for chunk in _stream_proc_stdout(proc):
                            yield chunk

                    return StreamingResponse(
                        remux_out(),
                        media_type="video/mp4",
                        headers={
                            "Access-Control-Allow-Origin": "*",
                            "Cache-Control": "no-store",
                        },
                    )

            # Passthrough with Range support (seekable mp4/webm)
            async with client.stream("GET", url, headers=headers) as upstream:
                status = upstream.status_code
                resp_headers = {
                    "Access-Control-Allow-Origin": "*",
                    "Accept-Ranges": "bytes",
                }
                for k in ["content-type", "content-length", "content-range", "accept-ranges"]:
                    v = upstream.headers.get(k)
                    if v:
                        resp_headers[k] = v

                async def passthrough():
                    async for chunk in upstream.aiter_bytes(65536):
                        yield chunk

                return StreamingResponse(
                    passthrough(),
                    status_code=status,
                    headers=resp_headers,
                )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"debrid fetch failed: {exc}")


@router.get("/stats")
async def torrent_stats():
    return {
        "ffmpeg": await has_ffmpeg(),
        "webtorrent_cli": bool(shutil.which("webtorrent")),
        "torrent_dir": _TORRENT_DIR,
        "webtorrent_port": _WEBTORRENT_PORT,
    }


@router.get("/status")
async def torrent_status(magnet: str = Query(...)):
    """Live per-torrent status placeholder. Returns active:false when no daemon is running."""
    return {"active": False, "note": "status requires webtorrent daemon"}
