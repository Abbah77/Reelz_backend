"""
api/insights.py — AI provider intelligence dashboard.

Endpoints:
    GET  /insights/providers          — ranked list of all providers with scores + explanations
    GET  /insights/providers/{id}     — deep report for one provider
    GET  /insights/anomalies          — providers currently showing anomalies
    POST /insights/providers/{id}/reset — clear all data for one provider
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Response
from typing import Optional

from api.envelope import ok
from api.cache_headers import set_cache
from ENGINE.manager.health import get_stats, get_insights, reset, score_for_ranking, ContentCategory

router = APIRouter(prefix="/insights", tags=["Insights"])


@router.get("/providers")
async def list_providers(
    response: Response,
    category: Optional[str] = Query(None, description="Filter/score for context: anime|asian|bollywood|movie|tv"),
):
    """
    Returns all providers ranked by composite AI score, with human-readable
    explanations for why each provider ranks where it does.

    Example response entry:
        {
          "id": "R-013",
          "name": "HDRezka",
          "score": { "composite": 87.4, "reliability": 35.1, "availability": 38.2, "context_fit": 14.1 },
          "explanation": "HDRezka (R-013): score 87.4/100, very reliable (96%), fast (820 ms avg), 3.2 qualities avg, HLS streams, subtitles available",
          "anomalies": []
        }
    """
    from ENGINE.providers.Stream.registry import get_all as stream_all
    from ENGINE.providers.Download.registry import get_all as download_all
    from ENGINE.providers.Subtitle.registry import get_all as subtitle_all

    cat: Optional[ContentCategory] = category if category in ("anime", "asian", "bollywood", "movie", "tv") else None  # type: ignore[assignment]

    all_providers = (
        [(p, "stream")   for p in stream_all()]
        + [(p, "download") for p in download_all()]
        + [(p, "subtitle") for p in subtitle_all()]
    )

    results = []
    for p, ptype in all_providers:
        insight = await get_insights(p.id, p.name, cat)
        insight["provider_type"] = ptype
        results.append(insight)

    # Sort by composite score descending
    results.sort(key=lambda x: x["score"]["composite"], reverse=True)

    set_cache(response, None)
    return ok({"providers": results, "category_filter": cat}, cache_ttl_ms=None)


@router.get("/providers/{provider_id}")
async def provider_detail(
    provider_id: str,
    response: Response,
    category: Optional[str] = Query(None),
):
    """Deep report for one provider across all time windows."""
    from ENGINE.providers.Stream.registry import get_all as stream_all
    from ENGINE.providers.Download.registry import get_all as download_all
    from ENGINE.providers.Subtitle.registry import get_all as subtitle_all

    all_providers = (
        list(stream_all()) + list(download_all()) + list(subtitle_all())
    )
    provider = next((p for p in all_providers if p.id == provider_id), None)
    name = provider.name if provider else provider_id

    cat: Optional[ContentCategory] = category if category in ("anime", "asian", "bollywood", "movie", "tv") else None  # type: ignore[assignment]
    insight = await get_insights(provider_id, name, cat)

    set_cache(response, None)
    return ok(insight, cache_ttl_ms=None)


@router.get("/anomalies")
async def list_anomalies(response: Response):
    """
    Returns only providers currently showing anomalies (latency spikes,
    success rate drops, error surges, open circuit breakers).
    Useful for monitoring dashboards and alerting.
    """
    from ENGINE.providers.Stream.registry import get_all as stream_all
    from ENGINE.providers.Download.registry import get_all as download_all
    from ENGINE.providers.Subtitle.registry import get_all as subtitle_all

    all_providers = (
        list(stream_all()) + list(download_all()) + list(subtitle_all())
    )

    flagged = []
    for p in all_providers:
        insight = await get_insights(p.id, p.name)
        if insight.get("anomalies") or insight.get("circuit_broken"):
            flagged.append({
                "id": p.id,
                "name": p.name,
                "circuit_broken": insight.get("circuit_broken", False),
                "score": insight["score"],
                "anomalies": insight.get("anomalies", []),
                "explanation": insight.get("explanation", ""),
            })

    flagged.sort(key=lambda x: x["score"]["composite"])  # worst first

    set_cache(response, None)
    return ok({"flagged": flagged, "count": len(flagged)}, cache_ttl_ms=None)


@router.post("/providers/{provider_id}/reset")
async def reset_provider(provider_id: str, response: Response):
    """Clear all data (events + circuit breaker) for one provider."""
    await reset(provider_id)
    set_cache(response, None)
    return ok({"provider_id": provider_id, "reset": True}, cache_ttl_ms=None)
