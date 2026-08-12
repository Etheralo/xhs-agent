from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Settings
from .llm import LLMEventCallback, chat_json
from .models import ContentBundle, FactSheet, Paper


XHS_TITLE_MAX_CHARS = 20
XHS_TITLE_SUMMARY_MAX_CHARS = 15
XHS_BODY_MAX_CHARS = 1000
XHS_BODY_GENERATION_TARGET = 900
XHS_SECTION_LABELS = (
    "论文题目",
    "会议来源",
    "研究摘要",
    "研究背景",
    "核心创新",
    "实验结果",
)


def xhs_title_is_valid(value: str) -> bool:
    if not value or "\n" in value or len(value) > XHS_TITLE_MAX_CHARS:
        return False
    parts = value.split("：", 1)
    return (
        len(parts) == 2
        and bool(parts[0].strip())
        and 1 <= len(parts[1].strip()) <= XHS_TITLE_SUMMARY_MAX_CHARS
    )


EMOJI = {
    "jailbreak_prompt_injection": "🛡️",
    "agent_tool_security": "🔐",
    "rag_memory_security": "🧠",
    "multimodal_safety": "👁️",
    "adversarial_robustness": "🧱",
    "poisoning_backdoor": "🧨",
    "privacy_data_leakage": "🔎",
    "alignment_governance": "⚖️",
    "safety_evaluation": "📏",
    "cybersecurity_agent": "🤖",
}


def slugify(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return result[:70] or "paper"


def output_directory(settings: Settings, paper: Paper, day: str) -> Path:
    return settings.output_dir / day / f"{paper.arxiv_id.replace('/', '-')}-{slugify(paper.title)}"


def _result_lines(facts: FactSheet) -> list[str]:
    if not facts.results:
        return ["这篇工作的主要贡献是方法、框架或问题定义；底稿中没有适合公开强调的量化数字。"]
    lines = []
    for result in facts.results[:3]:
        evidence = f"PDF 第 {','.join(map(str, result.source_pages))} 页"
        value = f"（{result.value}）" if result.value else ""
        lines.append(f"{result.claim}{value}｜证据：{evidence}")
    return lines


def _clean_prose(value: Any) -> str:
    text = str(value or "").replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return " ".join(line for line in lines if line).strip()


def _truncate(value: str, limit: int) -> str:
    value = _clean_prose(value)
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip("，,；;：:。.!！?？ ") + "…"


def _venue_abbreviation(paper: Paper) -> str:
    known = {
        "ieee_sp": "S&P",
        "usenix_security": "USENIX",
        "acm_ccs": "CCS",
        "ndss": "NDSS",
        "neurips": "NeurIPS",
        "icml": "ICML",
        "iclr": "ICLR",
        "aaai": "AAAI",
        "ijcai": "IJCAI",
        "acl": "ACL",
        "emnlp": "EMNLP",
        "cvpr": "CVPR",
        "iccv": "ICCV",
        "eccv": "ECCV",
    }
    if paper.venue_code in known:
        return known[str(paper.venue_code)]
    venue = _clean_prose(paper.venue)
    if paper.venue_status == "verified" and venue:
        tokens = re.findall(r"[A-Z][A-Z0-9&-]{1,8}", venue)
        if tokens:
            return tokens[0]
        initials = "".join(
            item[0] for item in re.findall(r"[A-Za-z]+", venue)
            if item.lower() not in {"on", "of", "and", "the"}
        ).upper()
        if initials:
            return initials[:6]
    return "ARXIV"


def _title_summary(facts: FactSheet) -> str:
    problem = _clean_prose(facts.problem.get("plain_cn") or "AI系统安全边界")
    short = re.split(r"[。！？!?；;，,：:]", problem)[0].strip()
    short = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", short)
    return short[:XHS_TITLE_SUMMARY_MAX_CHARS] or "AI系统安全边界"


def _xhs_title(paper: Paper, summary: str) -> str:
    venue = _venue_abbreviation(paper)
    clean_summary = re.sub(
        r"[^0-9A-Za-z\u4e00-\u9fff]+", "", _clean_prose(summary)
    )[:XHS_TITLE_SUMMARY_MAX_CHARS]
    if not clean_summary:
        clean_summary = _title_summary(FactSheet({}, {}, {}, [], [], [], []))
    available = XHS_TITLE_MAX_CHARS - len(venue) - 1
    if available < 6:
        venue = venue[: max(2, XHS_TITLE_MAX_CHARS - 7)]
        available = XHS_TITLE_MAX_CHARS - len(venue) - 1
    title = f"{venue}：{clean_summary[:available]}"
    if not xhs_title_is_valid(title):
        raise ValueError("生成的小红书标题不符合“会议简写：15字总结”格式。")
    return title


def _venue_line(paper: Paper) -> str:
    if paper.venue_status == "verified" and paper.venue:
        return _clean_prose(paper.venue)
    if paper.is_demo and paper.venue:
        return f"{_clean_prose(paper.venue)}（演示数据）"
    return "会议待核验（当前仅确认 arXiv 预印本）"


def _result_summary(facts: FactSheet) -> str:
    if not facts.results:
        return "论文事实底稿未提取到可公开强调的量化结果，发布前需回到实验章节核对。"
    lines: list[str] = []
    for item in facts.results[:3]:
        claim = _clean_prose(item.claim).rstrip("，,；;。.!！?？ ")
        value = f"，结果为{_clean_prose(item.value)}" if item.value else ""
        baseline = f"，对照为{_clean_prose(item.baseline)}" if item.baseline else ""
        pages = f"（PDF第{'、'.join(map(str, item.source_pages))}页）" if item.source_pages else ""
        lines.append(f"{claim}{value}{baseline}{pages}")
    return "；".join(lines) + "。"


def _local_xhs_sections(facts: FactSheet) -> dict[str, str]:
    problem = _clean_prose(
        facts.problem.get("plain_cn") or "原文未给出可确认的问题描述。"
    )
    method = _clean_prose(
        facts.method.get("one_sentence")
        or facts.method.get("plain_cn")
        or "原文方法仍需人工核对。"
    )
    example = _clean_prose(facts.method.get("plain_example"))
    background = problem
    if example:
        background = f"{problem}直观地说，{example}"
    return {
        "title_summary": _title_summary(facts),
        "research_summary": f"这项研究围绕上述安全问题提出可检查的方法，并用论文实验评估其效果与适用边界。",
        "research_background": background,
        "core_innovation": method,
    }


def _readable_xhs_stream(raw_preview: str) -> str:
    labels = {
        "title_summary": "标题总结",
        "research_summary": "研究摘要",
        "research_background": "研究背景",
        "core_innovation": "核心创新",
    }
    parts: list[str] = []
    for key, label in labels.items():
        match = re.search(
            rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)', raw_preview, re.S
        )
        if not match:
            continue
        encoded = match.group(1)
        try:
            value = json.loads(f'"{encoded}"')
        except json.JSONDecodeError:
            value = encoded.replace('\\n', "\n").replace('\\"', '"')
        value = _clean_prose(value)
        if value:
            parts.append(f"{label}\n{value}")
    return "\n\n".join(parts)


def _llm_xhs_sections(
    paper: Paper,
    facts: FactSheet,
    settings: Settings,
    on_event: LLMEventCallback | None,
) -> dict[str, str]:
    fallback = _local_xhs_sections(facts)
    fact_json = {
        "problem": facts.problem,
        "method": facts.method,
        "results": [item.to_dict() for item in facts.results],
        "uncertainties": facts.uncertainties,
    }
    prompt = f"""你是严谨的中文科技编辑。根据给定论文元数据和事实底稿，为小红书图文笔记撰写高信息密度、通俗但不夸张的内容。只返回 JSON 对象。

字段要求：
1. title_summary：12至15个字的中文核心总结，不含会议名、冒号、emoji、书名号或句末标点。
2. research_summary：60至100字，说明研究做了什么、解决什么，不重复论文题目。
3. research_background：80至140字，解释真实问题、已有困难和研究必要性。
4. core_innovation：80至160字，清楚说明方法机制、关键步骤和区别，不能只写“提出新框架”。

事实约束：只能改写事实底稿；不得补充底稿之外的数据、基线、会议录用信息或因果结论。不确定信息明确保守表达。语言自然连贯，避免“一字一行”、营销口号和空泛评价。

论文题目：{paper.title}
会议来源：{_venue_line(paper)}
事实底稿：{fact_json}
"""
    def readable_event(event: str, detail: dict[str, Any]) -> None:
        if on_event is None:
            return
        if event == "chunk" and detail.get("preview"):
            preview = _readable_xhs_stream(str(detail["preview"]))
            detail = {**detail, "preview": preview or "正在组织小红书正文…"}
        on_event(event, detail)

    raw = chat_json(
        settings, prompt, on_event=readable_event if on_event is not None else None
    )
    return {
        key: _clean_prose(raw.get(key)) or fallback[key]
        for key in fallback
    }


def _format_xhs_body(paper: Paper, sections: dict[str, str], facts: FactSheet) -> str:
    values = {
        "论文题目": _truncate(paper.title, 150),
        "会议来源": _truncate(_venue_line(paper), 70),
        "研究摘要": _truncate(sections["research_summary"], 130),
        "研究背景": _truncate(sections["research_background"], 170),
        "核心创新": _truncate(sections["core_innovation"], 190),
        "实验结果": _truncate(_result_summary(facts), 210),
    }

    def render() -> str:
        return "\n\n".join(f"{label}\n{values[label]}" for label in XHS_SECTION_LABELS)

    body = render()
    while len(body) > XHS_BODY_GENERATION_TARGET:
        label = max(values, key=lambda item: len(values[item]))
        current = values[label]
        if len(current) <= 24:
            break
        values[label] = _truncate(current, len(current) - min(20, len(body) - XHS_BODY_GENERATION_TARGET))
        body = render()
    if len(body) > XHS_BODY_MAX_CHARS:
        raise ValueError("生成的小红书正文超过平台字数限制。")
    return body


def render_wechat_html(settings: Settings, title: str, markdown: str) -> str:
    env = Environment(
        loader=FileSystemLoader(settings.root / "templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )
    return env.get_template("wechat/article.html.j2").render(
        title=title,
        markdown=markdown,
        paragraphs=[line for line in markdown.splitlines() if line.strip()],
    )


def generate_content(
    paper: Paper,
    facts: FactSheet,
    settings: Settings,
    *,
    on_event: LLMEventCallback | None = None,
) -> ContentBundle:
    sections = _local_xhs_sections(facts)
    if settings.llm_api_key and not paper.is_demo and on_event is not None:
        sections = _llm_xhs_sections(paper, facts, settings, on_event)
    title = _xhs_title(paper, sections["title_summary"])
    problem = str(facts.problem.get("plain_cn") or "原文未给出可确认的问题描述。")
    method = str(
        facts.method.get("one_sentence")
        or facts.method.get("plain_cn")
        or "原文方法仍需人工核对。"
    )
    example = str(facts.method.get("plain_example") or "暂无可靠的通俗例子。")
    results = _result_lines(facts)
    author_future = "；".join(facts.authors_future_work) or "作者没有在底稿中明确给出未来工作。"
    editorial = "；".join(facts.editorial_extension) or "我们认为可继续验证真实部署边界。"
    venue_line = _venue_line(paper)
    slides = [
        {"eyebrow": f"{paper.topic_label or 'AI Safety'} · {venue_line}", "title": title,
         "body": f"一篇论文，讲清问题、方法与可核对证据\n{paper.title}"},
        {"eyebrow": "WHY · 为什么要关心", "title": "风险发生在哪里？", "body": problem},
        {"eyebrow": "WHAT · 核心贡献", "title": "论文做了什么？", "body": method},
        {"eyebrow": "HOW · 通俗理解", "title": "方法怎么工作？", "body": example},
        {"eyebrow": "EVIDENCE · 结果与价值", "title": "证据能回到原文", "body": "\n".join(f"• {line}" for line in results)},
        {"eyebrow": "NEXT · 明确区分事实与推演", "title": "下一步能做什么？",
         "body": f"作者展望：{author_future}\n\n编辑推演：{editorial}\n\n原文：arXiv {paper.arxiv_id}"},
    ]

    result_md = "\n".join(f"- {line}" for line in results)
    author_md = "\n".join(f"- {item}" for item in facts.authors_future_work) or "- 原文底稿未确认明确的作者展望。"
    editorial_md = "\n".join(f"- {item}" for item in facts.editorial_extension) or "- 我们认为可继续验证真实部署边界。"
    uncertainties = "\n".join(f"- {item}" for item in facts.uncertainties) or "- 暂无额外不确定项。"
    wechat = f"""# {title}

> 原论文：{paper.title}  
> 作者：{'、'.join(paper.authors)}  
> 来源：{venue_line}  
> arXiv：[{paper.arxiv_id}]({paper.pdf_url})

## 先说结论

这篇工作值得关注，不只是因为它讨论了“{paper.topic_label or 'AI Safety'}”，更因为它把一个容易停留在口号层面的安全问题变成了可以描述、实现和检查的研究任务。下面所有论文事实都来自同一份事实底稿；涉及实验数字时保留 PDF 页码，无法确认的信息则放到“不确定项”，不靠猜测补齐故事。

## 问题是什么

{problem}

把它放进真实系统里看，风险往往不是单个模型回答得好不好，而是输入来源、上下文、模型判断和后续动作连成一条链。只看最终输出，可能看不到风险从哪里进入；只做一次静态测试，也未必覆盖部署时不断变化的输入。论文的价值首先在于把需要保护的边界说清楚，让后续防护和评测有共同对象。

## 核心方法

{method}

一个更直观的理解是：{example}

这里应关注方法解决了哪一步、依赖什么假设，以及它没有承诺什么。编辑审核时需要回到原文方法章节检查术语、流程和适用范围，避免把“在实验设置中有效”写成“在所有真实系统中都有效”。

## 结果与证据

{result_md}

这些结果的作用是支持论文自己的结论，不代表已经覆盖所有模型、数据和攻击者。若底稿没有适合公开的量化数字，我们就把贡献准确写成框架、数据集、定义或方法，而不是为了封面效果制造一个数字。

## 为什么值得看

对研究者，这项工作提供了一个可以复现、比较或继续扩展的起点；对工程团队，它提示安全设计必须落到明确的数据来源、决策节点和执行边界；对普通读者，它展示了 AI 安全并非抽象担忧，而是能够被拆成具体问题、验证证据和责任边界。真正有用的解读，不是把摘要翻译一遍，而是让读者知道结论在什么条件下成立。

## 作者展望

{author_md}

## 编辑推演（不是作者结论）

{editorial_md}

上面这一栏是本账号基于论文问题的延伸思考，不能写成“作者提出”。后续如果要形成研究选题，应重新做相关工作检索、确认新颖性，并把可验证的假设和评价指标单独列出。

## 审核时仍需确认

{uncertainties}

## 原文入口

- PDF：{paper.pdf_url}
- 会议证据：{paper.venue_evidence_url or '尚无已核验的官方页面'}
- 主题：{paper.topic_label or paper.topic or '待确认'}

如果你希望继续看这类“有证据、能落地”的 AI Safety 论文解读，可以收藏本文；发布前请以原论文和会议官方页面为准。
"""
    html = render_wechat_html(settings, title, wechat)
    caption = _format_xhs_body(paper, sections, facts)
    return ContentBundle(
        title=title,
        caption=caption,
        slides=slides,
        wechat_markdown=wechat,
        wechat_html=html,
        xhs_title=title,
    )
