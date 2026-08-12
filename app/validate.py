from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .generate import XHS_BODY_MAX_CHARS, XHS_SECTION_LABELS, xhs_title_is_valid
from .models import ContentBundle, FactSheet, Paper


@dataclass(slots=True)
class ValidationReport:
    passed: bool
    errors: list[str]
    warnings: list[str]
    checks: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "errors": self.errors,
            "warnings": self.warnings,
            "checks": self.checks,
        }


def validate_bundle(
    paper: Paper, facts: FactSheet, content: ContentBundle, settings: Settings
) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool] = {}

    checks["paper_identity_consistent"] = (
        facts.paper.get("arxiv_id") == paper.arxiv_id
        and facts.paper.get("title") == paper.title
    )
    if not checks["paper_identity_consistent"]:
        errors.append("事实底稿与候选论文身份不一致。")

    checks["venue_evidence_present"] = (
        paper.venue_status != "verified" or bool(paper.venue_evidence_url)
    )
    if not checks["venue_evidence_present"]:
        errors.append("verified 会议标签缺少外部证据 URL。")

    source_sections = [facts.problem, facts.method]
    checks["core_claims_have_pages"] = all(
        section.get("source_pages")
        and all(isinstance(page, int) and page > 0 for page in section["source_pages"])
        for section in source_sections
    )
    if not checks["core_claims_have_pages"]:
        errors.append("问题或方法缺少有效 PDF 页码。")

    checks["results_have_pages"] = all(
        result.source_pages and all(page > 0 for page in result.source_pages)
        for result in facts.results
    )
    if not checks["results_have_pages"]:
        errors.append("量化结果缺少有效 PDF 页码。")

    checks["six_cards"] = len(content.slides) == settings.xhs_slide_count
    if not checks["six_cards"]:
        errors.append(f"小红书卡片应为 {settings.xhs_slide_count} 张。")

    xhs_title = content.xhs_title or content.title
    structured_xhs = bool(content.xhs_title)
    checks["channel_identity_consistent"] = (
        paper.title in content.wechat_markdown and paper.title in content.caption
        if structured_xhs
        else all(
            token in content.wechat_markdown and token in content.caption
            for token in (paper.arxiv_id, content.title)
        )
    )
    if not checks["channel_identity_consistent"]:
        errors.append("小红书与公众号的标题或 arXiv ID 不一致。")

    checks["xhs_title_within_limit"] = (
        xhs_title_is_valid(xhs_title)
        if structured_xhs
        else bool(xhs_title.strip()) and "\n" not in xhs_title and len(xhs_title) <= 20
    )
    if not checks["xhs_title_within_limit"]:
        errors.append("小红书标题必须采用“会议简写：15字内总结”格式，且总长不超过 20 字。")

    checks["xhs_body_within_limit"] = (
        bool(content.caption.strip()) and len(content.caption) <= XHS_BODY_MAX_CHARS
    )
    if not checks["xhs_body_within_limit"]:
        errors.append(f"小红书正文必须在 {XHS_BODY_MAX_CHARS} 字以内。")

    checks["xhs_sections_complete"] = (
        all(label in content.caption for label in XHS_SECTION_LABELS)
        if structured_xhs
        else True
    )
    if not checks["xhs_sections_complete"]:
        errors.append("小红书正文缺少规定的论文解读部分。")

    checks["editorial_boundary_labeled"] = (
        "编辑推演" in content.wechat_markdown
        and "编辑推演" in content.slides[-1].get("body", "")
        and "不是作者结论" in content.wechat_markdown
    )
    if not checks["editorial_boundary_labeled"]:
        errors.append("编辑推演未与作者结论明确分开。")

    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", content.wechat_markdown))
    checks["wechat_length"] = settings.wechat_min_chars <= chinese_chars <= settings.wechat_max_chars
    if not checks["wechat_length"]:
        errors.append(
            f"公众号正文中文字符数 {chinese_chars}，应在 "
            f"{settings.wechat_min_chars}–{settings.wechat_max_chars} 之间。"
        )

    if facts.uncertainties:
        warnings.extend(facts.uncertainties)
    if paper.venue_status != "verified":
        warnings.append("会议状态未由官方或人工覆盖配置核验。")
    return ValidationReport(not errors, errors, warnings, checks)
