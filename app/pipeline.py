from __future__ import annotations

import json
import traceback
from dataclasses import fields
from datetime import date
from pathlib import Path
from typing import Callable, Iterable

from .artifacts import write_draft_artifacts, write_json
from .classify import classify_paper, score_paper
from .config import Settings
from .enrich import verify_venue
from .extract import claims_from_fact_sheet, extract_fact_sheet
from .generate import generate_content, output_directory
from .ingest import deduplicate_in_memory, fetch_arxiv, load_demo_papers
from .models import Paper, RunResult
from .render import render_cards, render_pdf_pages
from .storage import Storage


ProgressCallback = Callable[[str, str, str | None], None]
from .validate import validate_bundle


def paper_from_row(row: dict) -> Paper:
    names = {field.name for field in fields(Paper)}
    return Paper(**{key: value for key, value in row.items() if key in names})


class Pipeline:
    def __init__(self, settings: Settings, storage: Storage | None = None):
        self.settings = settings
        self.settings.ensure_dirs()
        self.storage = storage or Storage(settings.db_path)
        self.storage.initialize()

    def _model_event_callback(
        self,
        paper_id: int,
        run_id: int,
        notify: ProgressCallback,
        operation: str,
    ) -> Callable[[str, dict], None]:
        def callback(event: str, detail: dict) -> None:
            safe_detail = {**detail, "operation": operation}
            if event == "started":
                notify(
                    "model",
                    f"模型已开始{operation} · 输入 {detail.get('input_chars', 0):,} 字符",
                    None,
                )
                self.storage.log_event(
                    "llm_request_started",
                    paper_id=paper_id,
                    run_id=run_id,
                    detail=json.dumps(safe_detail, ensure_ascii=False),
                )
            elif event == "chunk":
                preview = detail.get("preview") if operation == "小红书内容生成" else None
                notify(
                    "writing" if preview else "model",
                    f"正在接收模型响应 · {detail.get('received_chars', 0):,} 字符",
                    str(preview) if preview else None,
                )
            elif event == "completed":
                notify(
                    "model",
                    f"模型{operation}完成 · 用时 {detail.get('duration_ms', 0) / 1000:.1f} 秒",
                    None,
                )
                self.storage.log_event(
                    "llm_request_completed",
                    paper_id=paper_id,
                    run_id=run_id,
                    detail=json.dumps(safe_detail, ensure_ascii=False),
                )
            elif event == "failed":
                notify("model", f"模型{operation}未完成", None)
                self.storage.log_event(
                    "llm_request_failed",
                    paper_id=paper_id,
                    run_id=run_id,
                    detail=json.dumps(safe_detail, ensure_ascii=False),
                )
            elif event == "fallback":
                notify("model", "模型分类失败，已使用本地规则继续", None)
                self.storage.log_event(
                    "llm_rule_fallback",
                    paper_id=paper_id,
                    run_id=run_id,
                    detail=json.dumps(safe_detail, ensure_ascii=False),
                )

        return callback

    def _generate_draft(
        self,
        paper_id: int,
        paper: Paper,
        run_id: int,
        progress: ProgressCallback | None = None,
    ) -> tuple[bool, list[str]]:
        """Generate and validate one selected paper, resuming safe partial states."""
        notify = progress or (lambda _stage, _message, _preview=None: None)
        current = str(self.storage.get_paper(paper_id)["status"])
        notify("reading", "正在读取论文并抽取事实", None)
        facts, source_path = extract_fact_sheet(
            paper,
            self.settings,
            on_event=self._model_event_callback(
                paper_id, run_id, notify, "事实抽取"
            ),
        )
        fact_preview = str(facts.problem.get("plain_cn") or facts.problem.get("summary") or "")
        notify("facts", "事实底稿已生成", fact_preview)
        if source_path:
            paper.source_text_path = str(source_path)
            self.storage.update_fields(paper_id, source_text_path=paper.source_text_path)
        self.storage.replace_claims(paper_id, claims_from_fact_sheet(facts))
        if current == "selected":
            self.storage.transition(paper_id, "extracted", run_id=run_id)

        current = str(self.storage.get_paper(paper_id)["status"])
        notify("writing", "正在生成小红书标题与结构化正文", "")
        content = generate_content(
            paper,
            facts,
            self.settings,
            on_event=self._model_event_callback(
                paper_id, run_id, notify, "小红书内容生成"
            ),
        )
        notify("writing", "小红书标题与正文已生成", f"{content.xhs_title}\n\n{content.caption}")
        if current == "extracted":
            self.storage.transition(paper_id, "drafted", run_id=run_id)

        target = output_directory(self.settings, paper, date.today().isoformat())
        notify("rendering", "正在导出论文配图和内容文件", content.caption)
        write_draft_artifacts(target, paper, facts, content)
        if source_path:
            publication_images = render_pdf_pages(source_path, target, page_count=6)
            image_source = "pdf_first_pages"
            page_numbers = list(range(1, len(publication_images) + 1))
        else:
            # Constructed demos do not have a real PDF. Keep a six-image fallback
            # so the offline workflow remains testable without implying PDF evidence.
            publication_images = render_cards(content.slides[:6], target, self.settings)
            image_source = "demo_generated_fallback"
            page_numbers = []
        write_json(
            target / "publication-images.json",
            {
                "source": image_source,
                "files": [path.name for path in publication_images],
                "page_numbers": page_numbers,
            },
        )
        report = validate_bundle(paper, facts, content, self.settings)
        notify(
            "validation",
            "自动检查已通过" if report.passed else "自动检查发现问题",
            content.caption,
        )
        write_json(target / "validation.json", report.to_dict())
        self.storage.save_draft(
            paper_id,
            "xhs",
            {
                "title": content.xhs_title,
                "body": content.caption,
                "slides": content.slides,
            },
            target, "passed" if report.passed else "failed",
        )
        self.storage.save_draft(
            paper_id, "wechat", {"title": content.title}, target,
            "passed" if report.passed else "failed",
        )
        current = str(self.storage.get_paper(paper_id)["status"])
        if report.passed and current == "drafted":
            self.storage.transition(
                paper_id, "ready_for_review", run_id=run_id, detail=str(target)
            )
            notify("review", "内容已生成，等待人工审核", content.caption)
        return report.passed, report.errors

    def generate_one(
        self,
        paper_id: int,
        progress: ProgressCallback | None = None,
    ) -> dict[str, object]:
        """Screen and generate one editor-selected paper for the web console."""
        notify = progress or (lambda _stage, _message, _preview=None: None)
        run_id = self.storage.start_run("console")
        error: str | None = None
        try:
            row = self.storage.get_paper(paper_id)
            if not row:
                raise ValueError(f"Unknown paper id: {paper_id}")
            if row["status"] in {"ready_for_review", "approved", "published"}:
                return {
                    "run_id": run_id,
                    "paper_id": paper_id,
                    "status": row["status"],
                    "message": "内容包已经生成，无需重复处理。",
                }
            if row["status"] == "rejected":
                self.storage.delete_paper(paper_id)
                notify("discarded", "旧的拒绝记录已删除", None)
                return {
                    "run_id": run_id,
                    "paper_id": paper_id,
                    "status": "discarded",
                    "message": "旧的拒绝记录已删除。",
                }

            paper = paper_from_row(row)
            classification = None
            if row["status"] == "discovered":
                notify("screening", "正在判断论文与选题的相关性", None)
                classification = classify_paper(
                    paper,
                    self.settings,
                    on_event=self._model_event_callback(
                        paper_id, run_id, notify, "选题分类"
                    ),
                )
                paper.topic = classification.topic
                paper.topic_label = classification.topic_label
                paper.metadata["classification"] = classification.to_dict()
                self.storage.update_fields(
                    paper_id,
                    topic=paper.topic,
                    topic_label=paper.topic_label,
                    metadata_json=json.dumps(paper.metadata, ensure_ascii=False),
                )
                self.storage.transition(paper_id, "screened", run_id=run_id)
                if not classification.is_in_scope:
                    reason = classification.reject_reason or "论文不在当前 AI Safety 选题范围内。"
                    self.storage.delete_paper(paper_id)
                    notify("discarded", "论文不符合选题范围，记录已删除", reason)
                    return {
                        "run_id": run_id,
                        "paper_id": paper_id,
                        "status": "discarded",
                        "message": reason,
                    }

            row = self.storage.get_paper(paper_id)
            assert row is not None
            paper = paper_from_row(row)
            if row["status"] == "screened":
                if classification is None:
                    raw_classification = paper.metadata.get("classification")
                    if isinstance(raw_classification, dict):
                        from .models import Classification

                        classification = Classification(**raw_classification)
                    else:
                        classification = classify_paper(
                            paper,
                            self.settings,
                            on_event=self._model_event_callback(
                                paper_id, run_id, notify, "选题分类"
                            ),
                        )
                if paper.venue_status != "verified" and not paper.is_demo:
                    paper = verify_venue(paper, self.settings, online=True)
                paper.score = score_paper(paper, classification, self.settings)
                self.storage.update_fields(
                    paper_id,
                    venue=paper.venue,
                    venue_code=paper.venue_code,
                    venue_status=paper.venue_status,
                    venue_evidence_url=paper.venue_evidence_url,
                    score=paper.score,
                    metadata_json=json.dumps(paper.metadata, ensure_ascii=False),
                )
                verified = paper.venue_status == "verified" or paper.is_demo
                detail = (
                    "source verified" if verified
                    else "source unverified; reminder only"
                )
                notify(
                    "source",
                    "来源已核验" if verified else "来源尚未核验，将作为发布前提醒",
                    None,
                )
                self.storage.transition(paper_id, "venue_verified", run_id=run_id, detail=detail)

            row = self.storage.get_paper(paper_id)
            assert row is not None
            if row["status"] == "venue_verified":
                self.storage.transition(paper_id, "selected", run_id=run_id)
            row = self.storage.get_paper(paper_id)
            assert row is not None
            if row["status"] not in {"selected", "extracted", "drafted"}:
                raise ValueError(f"当前状态无法生成：{row['status']}")
            paper = paper_from_row(row)
            passed, errors = self._generate_draft(
                paper_id, paper, run_id, progress=progress
            )
            if not passed:
                raise ValueError("内容校验未通过：" + "；".join(errors))
            return {
                "run_id": run_id,
                "paper_id": paper_id,
                "status": "ready_for_review",
                "message": "内容已生成并通过自动校验。",
            }
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.storage.log_event("console_generation_error", paper_id=paper_id, run_id=run_id, detail=error)
            raise
        finally:
            final = self.storage.get_paper(paper_id)
            ready = bool(final and final["status"] == "ready_for_review")
            self.storage.finish_run(
                run_id,
                candidates=1,
                accepted=1 if ready else 0,
                selected=1,
                error=error,
            )

    def run(
        self,
        *,
        demo: bool = False,
        fixture: Path | None = None,
        select_count: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> RunResult:
        notify = progress or (lambda _stage, _message, _preview=None: None)
        mode = "demo" if demo or fixture else "online"
        run_id = self.storage.start_run(mode)
        errors: list[str] = []
        selected_ids: list[int] = []
        ready: list[str] = []
        rejected = 0
        fetched_count = 0
        screened_count = 0
        qualifying: list[tuple[int, Paper]] = []
        try:
            notify("discovery", "正在获取最新论文", None)
            if demo or fixture:
                source = fixture or self.settings.root / "examples" / "demo_papers.json"
                papers = load_demo_papers(source)
            else:
                papers = fetch_arxiv(self.settings)
            papers = deduplicate_in_memory(papers)
            fetched_count = len(papers)
            candidates: list[tuple[int, Paper]] = []
            for paper in papers:
                paper_id, created = self.storage.upsert_paper(paper)
                if not created:
                    self.storage.log_event(
                        "duplicate_skipped", paper_id=paper_id, run_id=run_id,
                        detail=paper.arxiv_id,
                    )
                    continue
                candidates.append((paper_id, paper))

            for paper_id, paper in candidates:
                try:
                    classification = classify_paper(
                        paper,
                        self.settings,
                        on_event=self._model_event_callback(
                            paper_id, run_id, notify, "选题分类"
                        ),
                    )
                    paper.topic = classification.topic
                    paper.topic_label = classification.topic_label
                    paper.metadata["classification"] = classification.to_dict()
                    self.storage.update_fields(
                        paper_id,
                        topic=paper.topic,
                        topic_label=paper.topic_label,
                        metadata_json=json.dumps(paper.metadata, ensure_ascii=False),
                    )
                    self.storage.transition(paper_id, "screened", run_id=run_id)
                    screened_count += 1
                    if not classification.is_in_scope:
                        self.storage.delete_paper(paper_id)
                        rejected += 1
                        continue
                    paper = verify_venue(paper, self.settings, online=not paper.is_demo)
                    paper.score = score_paper(paper, classification, self.settings)
                    self.storage.update_fields(
                        paper_id,
                        venue=paper.venue,
                        venue_code=paper.venue_code,
                        venue_status=paper.venue_status,
                        venue_evidence_url=paper.venue_evidence_url,
                        score=paper.score,
                        metadata_json=json.dumps(paper.metadata, ensure_ascii=False),
                    )
                    verified = paper.venue_status == "verified" or paper.is_demo
                    detail = "source verified" if verified else "source unverified; reminder only"
                    self.storage.transition(
                        paper_id, "venue_verified", run_id=run_id, detail=detail
                    )
                    qualifying.append((paper_id, paper))
                except Exception as exc:  # preserve other candidates and audit the exact failure
                    message = f"{paper.arxiv_id}: {type(exc).__name__}: {exc}"
                    errors.append(message)
                    self.storage.log_event(
                        "candidate_error", paper_id=paper_id, run_id=run_id,
                        detail=message,
                    )

            # Papers that passed the gate but were below yesterday's Top N remain
            # eligible. Dedupe prevents rediscovery, not later editorial selection.
            qualifying_ids = {paper_id for paper_id, _ in qualifying}
            for row in self.storage.list_papers("venue_verified"):
                if row["id"] not in qualifying_ids:
                    qualifying.append((int(row["id"]), paper_from_row(row)))

            count = select_count if select_count is not None else self.settings.select_count
            qualifying.sort(key=lambda item: item[1].score, reverse=True)
            for paper_id, paper in qualifying[: max(0, count)]:
                self.storage.transition(paper_id, "selected", run_id=run_id)
                selected_ids.append(paper_id)
                try:
                    passed, validation_errors = self._generate_draft(
                        paper_id, paper, run_id, progress=progress
                    )
                    if passed:
                        ready.append(paper.arxiv_id)
                    else:
                        errors.append(
                            f"{paper.arxiv_id}: validation failed: "
                            + "; ".join(validation_errors)
                        )
                except Exception as exc:
                    message = f"{paper.arxiv_id}: {type(exc).__name__}: {exc}"
                    errors.append(message)
                    self.storage.log_event(
                        "generation_error", paper_id=paper_id, run_id=run_id,
                        detail=message + "\n" + traceback.format_exc(limit=4),
                    )
        except Exception as exc:
            errors.append(f"run: {type(exc).__name__}: {exc}")
        finally:
            self.storage.finish_run(
                run_id,
                candidates=fetched_count,
                accepted=len(ready),
                selected=len(selected_ids),
                error="\n".join(errors) or None,
            )
        selected_arxiv = [
            str(self.storage.get_paper(paper_id)["arxiv_id"]) for paper_id in selected_ids
        ]
        return RunResult(
            run_id=run_id,
            candidates=fetched_count,
            screened=screened_count,
            selected=selected_arxiv,
            ready_for_review=ready,
            rejected=rejected,
            errors=errors,
        )
