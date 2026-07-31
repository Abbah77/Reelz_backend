# Reelz Stream Engine — Python FastAPI Backend

A high-performance Python rewrite of the Node StreamPlay backend, designed to **beat Node in every measurable dimension**: speed, throughput, memory, and scraper coverage.

---

## Why Python beats Node here

| Concern | Node (StreamPlay) | Python (this) |
|---|---|---|
| HTTP client | axios (per-request overhead) | httpx HTTP/2 pooled — TLS sessions reused across all provider calls |
| Event loop | libuv | **uvloop** (same libuv underneath, minus JS overhead) |
| HTTP parser | llhttp | **httptools** (same C library as Node internally) |
| JSON | stdlib JSON | **orjson** — 5-10× faster, zero-copy |
| HTML parser | cheerio (JS) | **lxml** — C-level libxml2, 3-5× faster |
| Concurrency | Promise.allSettled | `asyncio.gather` — true fan-out, no JS event-loop tick cost |
| Cache | node-cache | In-process TTL-LRU with orjson serialisation |
| Provider count | ~20 | **25+ with sub-providers** |

---

## Architecture

```
app/
├── __init__.py          # FastAPI app, CORS, middleware, lifespan
├── config.py            # Pydantic Settings (reads .env)
├── models.py            # All request/response types (Pydantic v2)
├── cache.py             # In-memory TTL-LRU cache (orjson, thread-safe)
├── resolver.py          # Content-kind detection + TMDB enrichment
├── orchestrator.py      # Provider fan-out, dedup, priority scoring, SSE
├── routes/
│   └── api.py           # All HTTP endpoints
├── providers/
│   ├── __init__.py      # Registry init — registers all 25+ providers
│   ├── base.py          # Provider ABC + safe_invoke()
│   ├── rivestream.py    # RiveStream (worker-key + service fan-out)
│   ├── direct_api.py    # AllMovieLand, 2Embed, VidFast, VidRock, Hexa, Xpass, Vaplayer, DahmerMovies
│   ├── indian.py        # VegaMovies, HDHub4u, FourKHdHub, RogMovies, MultiMovies,
│   │                    #   Movies4u, UHDMovies, MoviesMod, TopMovies, Bollyflix, CineMacity
│   ├── anime.py         # AniZone, AniNeko, AnimeNoSub, AnimeWorld, AllAnime
│   ├── specialty.py     # HDRezka, KissKh, CastleTV, Vidlink
│   └── subtitles.py     # OpenSubtitles, SubtitleAPI, WyzieSubs
└── utils/
    ├── http.py          # Shared httpx client, FlareSolverr, cookie jar
    ├── hostextractors.py # Dean-Edwards unpacker, StreamWish/HubCloud/GDFlix resolvers
    └── encdec.py        # enc-dec.app gated client (VidFast/Hexa/Vidlink)
```

---

## Endpoints

### `POST /api/v1/streams`
Fan out to all providers concurrently. Returns deduplicated, priority-sorted streams.

```json
// Request
{
  "tmdb_id": 550,
  "type": "movie",
  "imdb_id": "tt0137523",
  "title": "Fight Club",
  "year": 1999
}

// Response
{
  "ok": true,
  "streams": [
    {
      "provider": "RiveStream",
      "provider_id": "rivestream",
      "name": "RiveStream PrimeVids (VLC)",
      "url": "https://cdn.rivestream.xyz/hls/tt0137523/master.m3u8",
      "type": "m3u8",
      "quality": "1080p",
      "language": "English",
      "headers": { "Referer": "https://rivestream.xyz/" },
      "playable": true,
      "priority": 0
    }
  ],
  "subtitles": [],
  "cached": false,
  "took_ms": 1840
}
```

### `GET /api/v1/streams/events` (SSE)
Real-time stream — fires an SSE `provider` event as each provider finishes, then a `done` event with the full sorted list. Ideal for showing a live "finding streams…" UI.

Query params: `tmdb_id`, `type`, `title`, `imdb_id`, `year`, `season`, `episode`

```
event: provider
data: {"id":"rivestream","state":"found","duration_ms":420,"links":[...]}

event: provider
data: {"id":"hdhub4u","state":"empty","duration_ms":3100,"links":[]}

event: done
data: {"streams":[...],"subtitles":[...],"total":12}
```

### `POST /api/v1/download`
Same shape as `/streams` but returns only stable direct-file links (mp4/mkv) from Indian scrapers.

### `POST /api/v1/subtitles`
```json
// Request
{ "tmdb_id": 550, "imdb_id": "tt0137523", "type": "movie", "languages": ["en", "fr"] }

// Response
{ "ok": true, "subtitles": [{ "provider": "opensubtitles", "language": "en", ... }] }
```

### `GET /api/v1/proxy?url=<url>&referer=<referer>`
Proxies Referer-gated HLS playlists and rewrites segment URLs back through the proxy so ExoPlayer needs no custom headers.

### `GET /api/v1/health`
Liveness probe.

### `GET /api/v1/stats`
Active provider count, disabled providers, cache size.

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure
```bash
cp .env.example .env
# Edit .env — minimum required: nothing (works out of the box)
# Recommended: TMDB_API_KEY, FLARESOLVERR_URL
```

### 3. Run
```bash
python main.py
# or
uvicorn app:app --host 0.0.0.0 --port 8000 --loop uvloop --http httptools
```

### 4. Docker (with FlareSolverr)
```bash
docker compose up -d
```

---

## Provider coverage

### Stream providers (25+)
| Provider | Kind | CF? | Notes |
|---|---|---|---|
| RiveStream | all | ✗ | Worker-key derivation + service fan-out |
| AllMovieLand | all | ✗ | Playlist-key API |
| 2Embed | movie/tv | ✗ | Iframe passthrough |
| VidFast | all | ✗ | enc-dec.app AES |
| VidRock | all | ✗ | enc-dec.app |
| Hexa | all | ✗ | enc-dec.app |
| Xpass | all | ✗ | |
| Vaplayer | all | ✗ | |
| DahmerMovies | all | ✗ | |
| Vidlink | all | ✗ | enc-dec.app |
| KissKh | asian | ✗ | Token API |
| CastleTV | all | ✗ | AES-128-CBC |
| HDRezka | movie/tv/asian | ✗ | Multi-audio, trash-decode |
| AniZone | anime | ✗/FS | |
| AniNeko | anime | FS | |
| AnimeNoSub | anime | ✗ | |
| AnimeWorld | all | ✗ | |
| AllAnime | anime | ✗ | AES-CTR + GraphQL |
| VegaMovies | movie/tv/asian | FS | V-Cloud host extractor |
| HDHub4u | movie/tv/asian | FS | Typesense search |
| FourKHdHub | movie/tv/asian | FS | |
| RogMovies | movie/tv/asian | FS | |
| MultiMovies | movie/tv/asian | FS | |
| Movies4u | movie/tv/asian | FS | |
| UHDMovies | movie/tv/asian | FS | |
| MoviesMod | movie/tv/asian | FS | |
| TopMovies | movie/tv/asian | FS | |
| Bollyflix | movie/tv/asian | FS | |
| CineMacity | movie/tv/asian | FS | |

**CF?** = needs FlareSolverr (FS) or not

### Host extractors (embedded)
StreamWish · Filelions · VidHide · Ridoo · DoodStream · VidGuard · HubCloud · GDFlix · Pixeldrain · Generic m3u8/mp4

### Subtitle providers
OpenSubtitles (direct API) · SubtitleAPI · WyzieSubs

---

## Priority scoring

Streams are automatically ranked:
1. **Type**: m3u8 HLS (10pts) > mp4 direct (6pts) > iframe (2pts)
2. **Quality**: 4K/2160p (+100) > 1080p (+80) > 720p (+60) > 480p (+40)
3. **Provider bonus**: RiveStream (+5), VidFast (+4), AllMovieLand/HDRezka (+3), CastleTV (+2)

The `priority` field in the response is the final rank (0 = best).

---

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `TMDB_API_KEY` | | Enables anime/asian auto-detection |
| `FLARESOLVERR_URL` | | Comma-sep list for CF bypass |
| `WARP_PROXY_URL` | | SOCKS5 for WARP routing |
| `PROVIDER_TIMEOUT_MS` | 45000 | Per-provider hard timeout |
| `CACHE_TTL_SECONDS` | 300 | Warm cache window |
| `CASTLE_SUFFIX` | | CastleTV AES key suffix (private) |
| `HDREZKA_BASE_URL` | `https://rezka.ag` | Mirror if main domain is blocked |
| `CORS_ORIGINS` | `*` | Comma-sep allowed origins |
