from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

import httpx

from .config import Settings
from .models import Paper


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _venue_entries(settings: Settings) -> list[dict[str, Any]]:
    return [
        item
        for group in ("primary", "supplementary")
        for item in settings.venues.get(group, [])
    ]


def match_venue(name: str | None, settings: Settings) -> dict[str, Any] | None:
    if not name:
        return None
    normalized = _normalized(name)
    for venue in _venue_entries(settings):
        aliases = [venue["display_name"], *venue.get("aliases", [])]
        if any(
            _normalized(alias) == normalized
            or _normalized(alias) in normalized
            or normalized in _normalized(alias)
            for alias in aliases
        ):
            return venue
    return None


def apply_manual_override(paper: Paper, settings: Settings) -> bool:
    override = settings.venue_overrides.get(paper.arxiv_id)
    if not isinstance(override, dict):
        return False
    paper.venue = override.get("venue")
    matched = match_venue(paper.venue, settings)
    paper.venue_code = override.get("venue_code") or (matched or {}).get("code")
    paper.venue_status = override.get("status", "unverified")
    paper.venue_evidence_url = override.get("evidence_url")
    return True


def _openalex_lookup(paper: Paper, settings: Settings) -> dict[str, Any] | None:
    params: dict[str, Any] = {"per-page": 5}
    if paper.doi:
        url = f"https://api.openalex.org/works/https://doi.org/{paper.doi}"
    else:
        url = "https://api.openalex.org/works"
        params["search"] = paper.title
    if settings.openalex_api_key:
        params["api_key"] = settings.openalex_api_key
    response = httpx.get(
        url,
        params=params,
        headers={"User-Agent": settings.user_agent},
        timeout=settings.request_timeout_seconds,
        follow_redirects=True,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    if "results" not in payload:
        return payload
    for item in payload.get("results", []):
        similarity = SequenceMatcher(
            None, _normalized(paper.title), _normalized(item.get("title", ""))
        ).ratio()
        if similarity >= 0.88:
            return item
    return None


def verify_venue(paper: Paper, settings: Settings, *, online: bool = True) -> Paper:
    if apply_manual_override(paper, settings):
        return paper
    if paper.is_demo and paper.venue_status == "verified":
        return paper
    if not online:
        return paper
    try:
        work = _openalex_lookup(paper, settings)
    except (httpx.HTTPError, ValueError):
        return paper
    if not work:
        return paper
    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    venue_name = source.get("display_name")
    matched = match_venue(venue_name, settings)
    if not matched:
        return paper
    # OpenAlex is useful matching evidence, but it is not an official acceptance
    # page. Keep the status explicit so a human can add a verified override.
    paper.venue = matched["display_name"]
    paper.venue_code = matched["code"]
    paper.venue_status = "matched_secondary"
    paper.venue_evidence_url = work.get("id")
    paper.metadata["openalex"] = {
        "work_id": work.get("id"),
        "source": venue_name,
        "doi": work.get("doi"),
    }
    return paper


def venue_points(paper: Paper, settings: Settings) -> int:
    if paper.venue_status != "verified":
        return 0
    matched = next(
        (item for item in _venue_entries(settings) if item.get("code") == paper.venue_code),
        None,
    )
    return int((matched or {}).get("points", 0))
