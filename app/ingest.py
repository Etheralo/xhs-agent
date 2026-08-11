from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

import httpx

from .config import Settings
from .models import Paper


ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def normalize_arxiv_id(value: str) -> str:
    value = value.rsplit("/", 1)[-1]
    return re.sub(r"v\d+$", "", value)


def _text(entry: ET.Element, path: str) -> str:
    node = entry.find(path, ATOM)
    return " ".join((node.text or "").split()) if node is not None else ""


def parse_arxiv_atom(payload: str) -> list[Paper]:
    root = ET.fromstring(payload)
    papers: list[Paper] = []
    for entry in root.findall("atom:entry", ATOM):
        raw_id = _text(entry, "atom:id")
        pdf_url = ""
        for link in entry.findall("atom:link", ATOM):
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = link.attrib.get("href", "")
                break
        authors = [
            _text(author, "atom:name")
            for author in entry.findall("atom:author", ATOM)
        ]
        categories = [
            node.attrib.get("term", "") for node in entry.findall("atom:category", ATOM)
        ]
        papers.append(
            Paper(
                arxiv_id=normalize_arxiv_id(raw_id),
                title=_text(entry, "atom:title"),
                abstract=_text(entry, "atom:summary"),
                authors=authors,
                published_at=_text(entry, "atom:published"),
                updated_at=_text(entry, "atom:updated"),
                categories=[item for item in categories if item],
                pdf_url=pdf_url or f"https://arxiv.org/pdf/{normalize_arxiv_id(raw_id)}",
                doi=_text(entry, "arxiv:doi") or None,
                journal_ref=_text(entry, "arxiv:journal_ref") or None,
                comment=_text(entry, "arxiv:comment") or None,
            )
        )
    return papers


def _keyword_match(paper: Paper, settings: Settings) -> bool:
    haystack = f"{paper.title} {paper.abstract}".lower()
    return any(
        keyword.lower() in haystack
        for topic in settings.topics.values()
        for keyword in topic.get("keywords", [])
    )


def fetch_arxiv(settings: Settings) -> list[Paper]:
    category_query = " OR ".join(f"cat:{item}" for item in settings.arxiv_categories)
    params = {
        "search_query": f"({category_query})",
        "start": 0,
        "max_results": settings.max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    response = httpx.get(
        ARXIV_API,
        params=params,
        headers={"User-Agent": settings.user_agent},
        timeout=settings.request_timeout_seconds,
        follow_redirects=True,
    )
    response.raise_for_status()
    cutoff = datetime.now(UTC) - timedelta(days=settings.days_back)
    result = []
    for paper in parse_arxiv_atom(response.text):
        try:
            published = datetime.fromisoformat(paper.published_at.replace("Z", "+00:00"))
        except ValueError:
            published = cutoff
        if published >= cutoff and _keyword_match(paper, settings):
            result.append(paper)
    return result


def search_arxiv(settings: Settings, query: str, *, max_results: int = 12) -> list[Paper]:
    """Search arXiv interactively without applying the scheduled topic/date gate."""
    words = re.findall(r"[\w.-]+", query, flags=re.UNICODE)
    if not words:
        return []
    search_query = " AND ".join(f"all:{word}" for word in words[:8])
    response = httpx.get(
        ARXIV_API,
        params={
            "search_query": search_query,
            "start": 0,
            "max_results": max(1, min(max_results, 25)),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
        headers={"User-Agent": settings.user_agent},
        timeout=settings.request_timeout_seconds,
        follow_redirects=True,
    )
    response.raise_for_status()
    papers = deduplicate_in_memory(parse_arxiv_atom(response.text))
    # Keep the ordering deterministic even when an API proxy or fixture does
    # not preserve arXiv's requested submittedDate order.
    return sorted(papers, key=lambda paper: paper.published_at or "", reverse=True)


def load_demo_papers(path: Path) -> list[Paper]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    result: list[Paper] = []
    for item in raw:
        known = {
            key: item[key]
            for key in (
                "arxiv_id", "title", "abstract", "authors", "published_at",
                "updated_at", "categories", "pdf_url", "doi", "journal_ref",
                "comment", "venue", "venue_code", "venue_status",
                "venue_evidence_url", "topic", "topic_label", "score",
            )
            if key in item
        }
        known["is_demo"] = True
        known["metadata"] = {
            "demo_facts": item["facts"],
            "source_pages": item.get("source_pages", []),
        }
        result.append(Paper(**known))
    return result


def deduplicate_in_memory(papers: Iterable[Paper]) -> list[Paper]:
    seen_ids: set[str] = set()
    seen_dois: set[str] = set()
    result: list[Paper] = []
    for paper in papers:
        doi = (paper.doi or "").lower().strip()
        if paper.arxiv_id in seen_ids or (doi and doi in seen_dois):
            continue
        seen_ids.add(paper.arxiv_id)
        if doi:
            seen_dois.add(doi)
        result.append(paper)
    return result
