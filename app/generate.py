from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Settings
from .models import ContentBundle, FactSheet, Paper


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


def _title(paper: Paper, facts: FactSheet) -> str:
    emoji = EMOJI.get(paper.topic or "", "🧭")
    problem = str(facts.problem.get("plain_cn") or "AI 系统的安全边界")
    short = re.split(r"[。！？!?；;]", problem)[0].strip()
    if len(short) > 22:
        short = short[:22]
    return f"{emoji}{short}，怎么守住？"


def generate_content(paper: Paper, facts: FactSheet, settings: Settings) -> ContentBundle:
    title = _title(paper, facts)
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
    venue_line = paper.venue if paper.venue_status == "verified" else "发表状态待人工核验"
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
    env = Environment(
        loader=FileSystemLoader(settings.root / "templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )
    html = env.get_template("wechat/article.html.j2").render(
        title=title,
        markdown=wechat,
        paragraphs=[line for line in wechat.splitlines() if line.strip()],
    )
    hashtags = " ".join(
        ["#AISafety", "#AI安全", f"#{paper.topic_label or '论文解读'}", "#科研"]
    )
    caption = f"{title}\n\n{problem}\n\n这篇论文用一个可检查的方法回应了这个问题。卡片里的结果均保留原文页码，编辑推演也与作者结论分开。\n\n原文：arXiv {paper.arxiv_id}\n{hashtags}"
    return ContentBundle(title, caption, slides, wechat, html)
