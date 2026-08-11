from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Paper:
    arxiv_id: str
    title: str
    abstract: str
    authors: list[str]
    published_at: str
    updated_at: str
    categories: list[str]
    pdf_url: str
    doi: str | None = None
    journal_ref: str | None = None
    comment: str | None = None
    venue: str | None = None
    venue_code: str | None = None
    venue_status: str = "unverified"
    venue_evidence_url: str | None = None
    topic: str | None = None
    topic_label: str | None = None
    score: float = 0.0
    status: str = "discovered"
    is_demo: bool = False
    source_text_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Classification:
    is_in_scope: bool
    topic: str | None
    topic_label: str | None
    reader_problem: str
    novelty: str
    evidence_strength: str
    relevance: int
    reader_value: int
    clarity: int
    verifiability: int
    reject_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FactClaim:
    claim: str
    value: str | None
    baseline: str | None
    source_pages: list[int]
    source_anchor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FactSheet:
    paper: dict[str, Any]
    problem: dict[str, Any]
    method: dict[str, Any]
    results: list[FactClaim]
    authors_future_work: list[str]
    editorial_extension: list[str]
    uncertainties: list[str]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["results"] = [claim.to_dict() for claim in self.results]
        return value


@dataclass(slots=True)
class ContentBundle:
    title: str
    caption: str
    slides: list[dict[str, str]]
    wechat_markdown: str
    wechat_html: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RunResult:
    run_id: int
    candidates: int
    screened: int
    selected: list[str]
    ready_for_review: list[str]
    rejected: int
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
