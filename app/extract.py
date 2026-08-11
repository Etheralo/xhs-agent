from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
import pymupdf as fitz

from .config import Settings
from .llm import LLMEventCallback, chat_json
from .models import FactClaim, FactSheet, Paper


def _text_items(value: Any) -> list[str]:
    """Normalize model text-or-list fields without iterating strings or dict keys."""
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        for key in ("plain_cn", "text", "content"):
            if key in value:
                return _text_items(value[key])
        return []
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            items.extend(_text_items(item))
        return items
    return []


def download_and_extract_pages(paper: Paper, settings: Settings) -> tuple[Path, list[str]]:
    cache_path = settings.data_dir / "cache" / f"{paper.arxiv_id.replace('/', '_')}.pdf"
    if not cache_path.exists():
        response = httpx.get(
            paper.pdf_url,
            headers={"User-Agent": settings.user_agent},
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
        )
        response.raise_for_status()
        if not response.content.startswith(b"%PDF"):
            raise ValueError(f"Not a PDF: {paper.pdf_url}")
        cache_path.write_bytes(response.content)
    document = fitz.open(cache_path)
    pages = [page.get_text("text").strip() for page in document]
    document.close()
    if not any(pages):
        raise ValueError("PDF contains no extractable text")
    return cache_path, pages


def _fact_sheet_from_dict(paper: Paper, raw: dict[str, Any]) -> FactSheet:
    results = [
        FactClaim(
            claim=str(item.get("claim", "")),
            value=str(item["value"]) if item.get("value") is not None else None,
            baseline=str(item["baseline"]) if item.get("baseline") is not None else None,
            source_pages=[int(page) for page in item.get("source_pages", [])],
            source_anchor=item.get("source_anchor"),
        )
        for item in raw.get("results", [])
    ]
    return FactSheet(
        paper={
            "title": paper.title,
            "authors": paper.authors,
            "arxiv_id": paper.arxiv_id,
            "doi": paper.doi,
            "venue": paper.venue,
            "venue_status": paper.venue_status,
            "venue_evidence_url": paper.venue_evidence_url,
            "pdf_url": paper.pdf_url,
        },
        problem=dict(raw.get("problem") or {}),
        method=dict(raw.get("method") or {}),
        results=results,
        authors_future_work=_text_items(raw.get("authors_future_work")),
        editorial_extension=_text_items(raw.get("editorial_extension")),
        uncertainties=_text_items(raw.get("uncertainties")),
    )


def _ranked_page_text(pages: list[str], max_chars: int) -> tuple[str, list[int]]:
    """Keep broad paper coverage while bounding a potentially huge model prompt."""
    keywords = re.compile(
        r"\b(?:abstract|introduction|method|methodology|approach|experiment|evaluation|"
        r"result|discussion|limitation|conclusion|future work)\b|"
        r"摘要|引言|方法|实验|评估|结果|讨论|局限|结论|未来工作",
        re.I,
    )
    ranked: list[tuple[int, int]] = []
    total_pages = len(pages)
    for index, text in enumerate(pages, 1):
        score = len(keywords.findall(text[:5000])) * 3
        if index <= 4:
            score += 20 - index
        if index > max(0, total_pages - 3):
            score += 12
        ranked.append((score, index))
    ranked.sort(key=lambda item: (-item[0], item[1]))

    selected: dict[int, str] = {}
    used = 0
    for _score, page_number in ranked:
        header = f"[PDF_PAGE_{page_number}]\n"
        remaining = max_chars - used - len(header) - 2
        if remaining < 300:
            break
        excerpt = pages[page_number - 1].strip()[: min(6000, remaining)]
        if not excerpt:
            continue
        selected[page_number] = f"{header}{excerpt}"
        used += len(header) + len(excerpt) + 2
    ordered_pages = sorted(selected)
    return "\n\n".join(selected[index] for index in ordered_pages), ordered_pages


def _llm_extract(
    paper: Paper,
    pages: list[str],
    settings: Settings,
    on_event: LLMEventCallback | None = None,
) -> FactSheet:
    page_text, included_pages = _ranked_page_text(pages, settings.llm_max_input_chars)
    prompt = f"""你是严谨的论文事实抽取员。只根据带页码的论文正文生成 JSON。
必须包含 problem, method, results, authors_future_work, editorial_extension,
uncertainties。problem/method 包含 plain_cn 和 source_pages；method 另含
one_sentence、plain_example。results 每项包含 claim、value、baseline、
source_pages、source_anchor。找不到数字就让 results 为空，禁止猜测。
作者展望和编辑推演必须分开；编辑推演必须用“我们认为”表述。
论文：{paper.title}
摘要：{paper.abstract}
本次提供的 PDF 页码：{included_pages}
{page_text}
"""
    raw = chat_json(
        settings,
        prompt,
        on_event=on_event,
    )
    return _fact_sheet_from_dict(paper, raw)


def _conservative_extract(paper: Paper, pages: list[str]) -> FactSheet:
    abstract = paper.abstract.strip()
    sentences = re.split(r"(?<=[.!?])\s+", abstract)
    problem = sentences[0] if sentences else abstract
    method = sentences[1] if len(sentences) > 1 else "摘要未明确分离方法步骤。"
    return _fact_sheet_from_dict(
        paper,
        {
            "problem": {"plain_cn": problem, "source_pages": [1]},
            "method": {
                "one_sentence": method,
                "plain_example": "需要编辑结合论文方法章节补充通俗例子。",
                "source_pages": [1],
            },
            "results": [],
            "authors_future_work": [],
            "editorial_extension": ["我们认为可进一步评估真实系统中的适用边界。"],
            "uncertainties": ["未配置模型 API；当前底稿仅保守复述摘要，需人工核对全文。"],
        },
    )


def extract_fact_sheet(
    paper: Paper,
    settings: Settings,
    *,
    on_event: LLMEventCallback | None = None,
) -> tuple[FactSheet, Path | None]:
    if paper.is_demo:
        raw = paper.metadata.get("demo_facts") or {}
        return _fact_sheet_from_dict(paper, raw), None
    source_path, pages = download_and_extract_pages(paper, settings)
    if settings.llm_api_key:
        return _llm_extract(paper, pages, settings, on_event=on_event), source_path
    return _conservative_extract(paper, pages), source_path


def claims_from_fact_sheet(facts: FactSheet) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for claim_type, section in (("problem", facts.problem), ("method", facts.method)):
        text = section.get("plain_cn") or section.get("one_sentence") or ""
        for page in section.get("source_pages", []):
            claims.append(
                {"claim_type": claim_type, "claim_text": text,
                 "source_page": page, "source_anchor": None}
            )
    for result in facts.results:
        for page in result.source_pages:
            claims.append(
                {"claim_type": "result", "claim_text": result.claim,
                 "source_page": page, "source_anchor": result.source_anchor}
            )
    return claims
