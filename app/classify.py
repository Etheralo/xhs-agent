from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import Settings
from .enrich import venue_points
from .llm import LLMEventCallback, chat_json
from .models import Classification, Paper


def _clamp(value: Any, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return minimum


def rule_classify(paper: Paper, settings: Settings) -> Classification:
    text = f"{paper.title} {paper.abstract}".lower()
    matches: list[tuple[str, str, int]] = []
    for code, config in settings.topics.items():
        count = sum(text.count(keyword.lower()) for keyword in config.get("keywords", []))
        if count:
            matches.append((code, str(config.get("label", code)), count))
    matches.sort(key=lambda item: item[2], reverse=True)
    if not matches:
        return Classification(
            is_in_scope=False,
            topic=None,
            topic_label=None,
            reader_problem="",
            novelty="",
            evidence_strength="low",
            relevance=0,
            reader_value=0,
            clarity=0,
            verifiability=0,
            reject_reason="No configured AI Safety / AI Security keyword matched.",
        )
    topic, label, count = matches[0]
    sentences = re.split(r"(?<=[.!?])\s+", paper.abstract.strip())
    reader_problem = sentences[0][:320] if sentences else paper.abstract[:320]
    novelty = sentences[1][:320] if len(sentences) > 1 else reader_problem
    has_number = bool(re.search(r"\b\d+(?:\.\d+)?%?\b", paper.abstract))
    return Classification(
        is_in_scope=True,
        topic=topic,
        topic_label=label,
        reader_problem=reader_problem,
        novelty=novelty,
        evidence_strength="medium" if has_number else "low",
        relevance=min(25, 17 + count * 2),
        reader_value=12,
        clarity=8 if len(sentences) >= 2 else 6,
        verifiability=8 if has_number else 5,
    )


def _llm_classify(
    paper: Paper,
    settings: Settings,
    on_event: LLMEventCallback | None = None,
) -> Classification:
    topics = {key: value.get("label") for key, value in settings.topics.items()}
    prompt = f"""你是 AI Safety 论文编辑。只依据给定标题和摘要分类，不补充外部事实。
可选主题：{json.dumps(topics, ensure_ascii=False)}
返回 JSON，字段必须是 is_in_scope, topic, reader_problem, novelty,
evidence_strength, scores, reject_reason。scores 包含 relevance(0-25),
reader_value(0-15), clarity(0-10), verifiability(0-10)。
标题：{paper.title}
摘要：{paper.abstract}
"""
    raw = chat_json(settings, prompt, on_event=on_event)
    topic = raw.get("topic")
    if topic not in settings.topics:
        topic = None
    scores = raw.get("scores") or {}
    return Classification(
        is_in_scope=bool(raw.get("is_in_scope")) and topic is not None,
        topic=topic,
        topic_label=(settings.topics.get(topic) or {}).get("label"),
        reader_problem=str(raw.get("reader_problem") or "")[:500],
        novelty=str(raw.get("novelty") or "")[:500],
        evidence_strength=str(raw.get("evidence_strength") or "low"),
        relevance=_clamp(scores.get("relevance"), 0, 25),
        reader_value=_clamp(scores.get("reader_value"), 0, 15),
        clarity=_clamp(scores.get("clarity"), 0, 10),
        verifiability=_clamp(scores.get("verifiability"), 0, 10),
        reject_reason=raw.get("reject_reason"),
    )


def classify_paper(
    paper: Paper,
    settings: Settings,
    on_event: LLMEventCallback | None = None,
) -> Classification:
    if settings.llm_api_key and not paper.is_demo:
        try:
            return _llm_classify(paper, settings, on_event=on_event)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            # A failed model call must not break discovery. The deterministic
            # result remains visibly lower confidence and is logged by pipeline.
            if on_event:
                try:
                    on_event(
                        "fallback",
                        {
                            "model": settings.llm_model,
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:500],
                        },
                    )
                except Exception:
                    pass
    return rule_classify(paper, settings)


def score_paper(
    paper: Paper, classification: Classification, settings: Settings
) -> float:
    try:
        published = datetime.fromisoformat(paper.published_at.replace("Z", "+00:00"))
        age_days = max(0, (datetime.now(UTC) - published).days)
        timeliness = max(0, 5 - age_days // 30)
    except ValueError:
        timeliness = 0
    return float(
        venue_points(paper, settings)
        + classification.relevance
        + classification.reader_value
        + classification.clarity
        + classification.verifiability
        + timeliness
    )
