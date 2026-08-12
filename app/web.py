from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .artifacts import create_approval_manifest, export_zip, publication_image_names
from .config import Settings
from .enrich import match_venue
from .ingest import search_arxiv
from .models import Paper
from .pipeline import Pipeline
from .publish import configured_publishers, publish_package
from .storage import Storage
from .xhs_browser import XHSLoginRequired, publish_to_xhs


REVIEW_CHECKLIST = [
    "论文确实值得发布",
    "问题和方法没有讲反",
    "核心数字能在标注页码找到",
    "通俗例子不误导",
    "编辑推演没有写成作者结论",
    "6 张发布配图清晰可读（真实论文为 PDF 前六页）",
]


class ConsoleService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.ensure_dirs()
        self.storage = Storage(settings.db_path)
        self.storage.initialize()
        self.pipeline = Pipeline(settings, self.storage)
        self.jobs: dict[str, dict[str, Any]] = {}
        self._active_unique_jobs: dict[str, str] = {}
        self._jobs_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="xhs-console")

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def overview(self) -> dict[str, Any]:
        with self.storage.connect() as db:
            counts = {
                str(row["status"]): int(row["count"])
                for row in db.execute(
                    "SELECT status, COUNT(*) AS count FROM papers GROUP BY status"
                ).fetchall()
            }
            last_run = db.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        with self._jobs_lock:
            active_jobs = sum(
                1 for job in self.jobs.values() if job["status"] in {"queued", "running"}
            )
        generated = sum(
            counts.get(status, 0)
            for status in ("ready_for_review", "approved", "published")
        )
        return {
            "total": sum(value for status, value in counts.items() if status != "rejected"),
            "ingested": sum(
                counts.get(status, 0)
                for status in ("discovered", "screened", "venue_verified", "selected", "extracted", "drafted")
            ),
            "ready": counts.get("ready_for_review", 0),
            "generated": generated,
            "approved": counts.get("approved", 0),
            "published": counts.get("published", 0),
            "active_jobs": active_jobs,
            "status_counts": counts,
            "last_run": dict(last_run) if last_run else None,
            "settings": {
                "model_configured": bool(self.settings.llm_api_key),
                "model_name": self.settings.llm_model,
                "openalex_configured": bool(self.settings.openalex_api_key),
                "days_back": self.settings.days_back,
                "select_count": self.settings.select_count,
                "publishers": configured_publishers(self.settings),
            },
        }

    def list_papers(self, query: str = "", status: str = "") -> list[dict[str, Any]]:
        papers = self.storage.list_papers(status or None)
        needle = query.casefold().strip()
        if needle:
            papers = [
                paper for paper in papers
                if needle in " ".join(
                    [
                        paper["title"],
                        paper["arxiv_id"],
                        " ".join(paper["authors"]),
                        paper.get("topic_label") or "",
                        paper.get("venue") or "",
                    ]
                ).casefold()
            ]
        return [self._paper_summary(paper) for paper in papers]

    @staticmethod
    def _paper_summary(paper: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": paper["id"],
            "arxiv_id": paper["arxiv_id"],
            "title": paper["title"],
            "authors": paper["authors"],
            "abstract": paper["abstract"],
            "published_at": paper["published_at"],
            "topic": paper.get("topic"),
            "topic_label": paper.get("topic_label"),
            "venue": paper.get("venue"),
            "venue_status": paper.get("venue_status"),
            "score": paper.get("score", 0),
            "status": paper["status"],
            "is_demo": paper["is_demo"],
            "rejection_reason": paper.get("rejection_reason"),
        }

    def search_remote(self, query: str) -> list[dict[str, Any]]:
        if len(query.strip()) < 2:
            raise ValueError("请输入至少 2 个字符的关键词。")
        existing = {paper["arxiv_id"]: paper["id"] for paper in self.storage.list_papers()}
        return [
            {
                **paper.to_dict(),
                "existing_id": existing.get(paper.arxiv_id),
            }
            for paper in search_arxiv(self.settings, query, max_results=12)
        ]

    def import_paper(self, payload: dict[str, Any]) -> dict[str, Any]:
        arxiv_id = str(payload.get("arxiv_id", "")).strip()
        if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})", arxiv_id, re.I):
            raise ValueError("arXiv ID 格式不正确。")
        title = str(payload.get("title", "")).strip()
        abstract = str(payload.get("abstract", "")).strip()
        if not title or not abstract:
            raise ValueError("论文标题和摘要不能为空。")
        paper = Paper(
            arxiv_id=arxiv_id,
            title=title,
            abstract=abstract,
            authors=[str(item) for item in payload.get("authors", []) if str(item).strip()],
            published_at=str(payload.get("published_at", "")),
            updated_at=str(payload.get("updated_at", payload.get("published_at", ""))),
            categories=[str(item) for item in payload.get("categories", [])],
            pdf_url=str(payload.get("pdf_url") or f"https://arxiv.org/pdf/{arxiv_id}"),
            doi=str(payload["doi"]) if payload.get("doi") else None,
            journal_ref=str(payload["journal_ref"]) if payload.get("journal_ref") else None,
            comment=str(payload["comment"]) if payload.get("comment") else None,
        )
        paper_id, created = self.storage.upsert_paper(paper)
        self.storage.log_event(
            "console_imported" if created else "console_duplicate_selected",
            paper_id=paper_id,
            detail=arxiv_id,
        )
        return {"paper_id": paper_id, "created": created}

    def _artifact_dir(self, paper_id: int) -> Path | None:
        with self.storage.connect() as db:
            row = db.execute(
                "SELECT artifact_path FROM drafts WHERE paper_id=? LIMIT 1", (paper_id,)
            ).fetchone()
        return Path(row["artifact_path"]) if row else None

    @staticmethod
    def _read_json(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    @staticmethod
    def _read_text(path: Path) -> str | None:
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def paper_detail(self, paper_id: int) -> dict[str, Any]:
        paper = self.storage.get_paper(paper_id)
        if not paper:
            raise KeyError("没有找到这篇论文。")
        with self.storage.connect() as db:
            claims = [
                dict(row) for row in db.execute(
                    "SELECT claim_type, claim_text, source_page, source_anchor "
                    "FROM claims WHERE paper_id=? ORDER BY id", (paper_id,)
                ).fetchall()
            ]
            events = [
                dict(row) for row in db.execute(
                    "SELECT event, detail, created_at FROM events WHERE paper_id=? "
                    "ORDER BY id DESC LIMIT 30", (paper_id,)
                ).fetchall()
            ]
        target = self._artifact_dir(paper_id)
        artifacts: dict[str, Any] | None = None
        if target and target.is_dir():
            image_manifest = self._read_json(target / "publication-images.json") or {}
            image_names = publication_image_names(target)
            images = [
                f"/api/artifacts/{paper_id}/{name}"
                for name in image_names
                if (target / name).is_file()
            ]
            zip_files = sorted(target.glob("*-approved.zip"))
            artifacts = {
                "images": images,
                "image_source": image_manifest.get("source") or "legacy_generated_cards",
                "image_page_numbers": image_manifest.get("page_numbers") or [],
                "caption": self._read_text(target / "xhs-caption.md"),
                "wechat_markdown": self._read_text(target / "wechat.md"),
                "slides": self._read_json(target / "xhs-slides.json"),
                "facts": self._read_json(target / "facts.json"),
                "validation": self._read_json(target / "validation.json"),
                "approved": (target / "FINAL-APPROVED.json").is_file(),
                "export_url": (
                    f"/api/artifacts/{paper_id}/{zip_files[-1].name}" if zip_files else None
                ),
            }
        return {
            "paper": paper,
            "claims": claims,
            "events": events,
            "artifacts": artifacts,
            "review_checklist": REVIEW_CHECKLIST,
            "publications": self.storage.list_publications(paper_id),
            "publishers": configured_publishers(self.settings),
        }

    def configure_venue(self, paper_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        paper = self.storage.get_paper(paper_id)
        if not paper:
            raise KeyError("没有找到这篇论文。")
        if paper["status"] == "published":
            raise ValueError("已发布论文不能再修改来源信息。")
        venue = str(payload.get("venue", "")).strip()
        evidence_url = str(payload.get("evidence_url", "")).strip()
        parsed = urlparse(evidence_url)
        if not venue or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("请填写会议名称和有效的官方证据网址。")
        self.storage.update_fields(
            paper_id,
            venue=venue,
            venue_code=(match_venue(venue, self.settings) or {}).get("code") or "manual",
            venue_status="verified",
            venue_evidence_url=evidence_url,
            rejection_reason=None,
        )
        self.storage.log_event(
            "manual_venue_verified", paper_id=paper_id, detail=f"{venue} | {evidence_url}"
        )
        return {"paper_id": paper_id, "venue": venue, "venue_status": "verified"}

    @staticmethod
    def _job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
        return {**job, "updates": [dict(item) for item in job.get("updates", [])]}

    def _submit(
        self,
        kind: str,
        callback: Callable[[Callable[[str, str, str | None], None]], Any],
        *,
        unique_key: str | None = None,
    ) -> dict[str, Any]:
        with self._jobs_lock:
            if unique_key:
                existing_id = self._active_unique_jobs.get(unique_key)
                existing = self.jobs.get(existing_id) if existing_id else None
                if existing and existing["status"] in {"queued", "running"}:
                    snapshot = self._job_snapshot(existing)
                    snapshot["deduplicated"] = True
                    return snapshot
            job_id = uuid.uuid4().hex[:12]
            job = {
                "id": job_id,
                "kind": kind,
                "status": "queued",
                "message": "任务已进入队列",
                "result": None,
                "error": None,
                "updates": [
                    {"stage": "queued", "message": "任务已进入队列", "preview": None}
                ],
            }
            self.jobs[job_id] = job
            if unique_key:
                self._active_unique_jobs[unique_key] = job_id

        def run() -> None:
            with self._jobs_lock:
                job.update(status="running", message="正在处理，请稍候")

            def progress(stage: str, message: str, preview: str | None = None) -> None:
                update = {"stage": stage, "message": message, "preview": preview}
                with self._jobs_lock:
                    job["message"] = message
                    job["updates"].append(update)

            try:
                result = callback(progress)
                with self._jobs_lock:
                    job.update(status="completed", message="任务已完成", result=result)
            except Exception as exc:
                with self._jobs_lock:
                    job.update(status="failed", message="任务未完成", error=str(exc))
            finally:
                if unique_key:
                    with self._jobs_lock:
                        if self._active_unique_jobs.get(unique_key) == job_id:
                            self._active_unique_jobs.pop(unique_key, None)

        self._executor.submit(run)
        return self._job_snapshot(job)

    def generate(self, paper_id: int) -> dict[str, Any]:
        if not self.storage.get_paper(paper_id):
            raise KeyError("没有找到这篇论文。")
        return self._submit(
            "generate",
            lambda progress: self.pipeline.generate_one(paper_id, progress=progress),
            unique_key=f"generate:{paper_id}",
        )

    def run_discovery(self, *, demo: bool, select_count: int) -> dict[str, Any]:
        return self._submit(
            "demo" if demo else "discovery",
            lambda progress: self.pipeline.run(
                demo=demo,
                select_count=max(1, min(select_count, 10)),
                progress=progress,
            ).to_dict(),
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            job = self.jobs.get(job_id)
            if not job:
                raise KeyError("没有找到这个任务。")
            return self._job_snapshot(job)

    def _publish_xhs_browser(
        self,
        paper: dict[str, Any],
        target: Path,
        progress: Callable[[str, str, str | None], None],
    ) -> dict[str, Any]:
        paper_id = int(paper["id"])

        def tracked_progress(stage: str, message: str, preview: str | None = None) -> None:
            self.storage.save_publication(paper_id, "xhs", stage, message=message)
            progress(stage, message, preview)

        try:
            outcome = publish_to_xhs(
                self.settings,
                paper=paper,
                artifact_dir=target,
                progress=tracked_progress,
            )
        except XHSLoginRequired as exc:
            self.storage.save_publication(paper_id, "xhs", "needs_login", message=str(exc))
            self.storage.log_event("xhs_login_required", paper_id=paper_id, detail=str(exc))
            raise
        except Exception as exc:
            self.storage.save_publication(paper_id, "xhs", "failed", message=str(exc))
            self.storage.log_event("xhs_fill_failed", paper_id=paper_id, detail=str(exc))
            raise

        self.storage.save_publication(
            paper_id,
            "xhs",
            outcome.status,
            external_id=outcome.external_id,
            external_url=outcome.external_url,
            message=outcome.message,
        )
        if outcome.status == "filled":
            self.storage.log_event(
                "xhs_content_filled", paper_id=paper_id, detail=outcome.message
            )
        return outcome.to_dict()

    def prepare_publication(self, paper_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        paper = self.storage.get_paper(paper_id)
        if not paper:
            raise KeyError("没有找到这篇论文。")
        reviewer = str(payload.get("reviewer", "")).strip()
        checks = payload.get("checks", [])
        channels = payload.get("channels", ["xhs", "wechat"])
        if not reviewer:
            raise ValueError("请填写审核人姓名。")
        if not isinstance(checks, list) or len(checks) != len(REVIEW_CHECKLIST) or not all(checks):
            raise ValueError(f"发布前必须完成全部 {len(REVIEW_CHECKLIST)} 项人工审核。")
        if (
            not isinstance(channels, list)
            or not channels
            or any(channel not in {"xhs", "wechat"} for channel in channels)
        ):
            raise ValueError("请选择有效的发布渠道。")
        target = self._artifact_dir(paper_id)
        if not target:
            raise ValueError("这篇论文还没有可发布的内容包。")
        if paper["status"] == "ready_for_review":
            create_approval_manifest(target, reviewer=reviewer)
            self.storage.approve(paper_id)
        elif paper["status"] not in {"approved", "published"}:
            raise ValueError(f"当前状态不能发布：{paper['status']}")
        output = export_zip(target)
        approval_digest = hashlib.sha256(
            (target / "FINAL-APPROVED.json").read_bytes()
        ).hexdigest()[:16]
        outcomes = []
        publication_job = None
        for channel in dict.fromkeys(channels):
            if channel == "xhs" and self.settings.xhs_publish_mode == "browser":
                existing = next(
                    (
                        item
                        for item in self.storage.list_publications(paper_id)
                        if item["channel"] == "xhs"
                    ),
                    None,
                )
                if existing and existing["status"] == "published":
                    outcomes.append(existing)
                    continue
                if not existing or existing["status"] not in {
                    "queued", "launching", "uploading", "filling"
                }:
                    self.storage.save_publication(
                        paper_id,
                        "xhs",
                        "queued",
                        message=f"小红书内容自动填充任务已进入队列。内容摘要：{approval_digest}",
                    )
                publication_job = self._submit(
                    "fill_xhs",
                    lambda progress, selected=dict(paper), folder=target: self._publish_xhs_browser(
                        selected, folder, progress
                    ),
                    unique_key=f"fill:xhs:{paper_id}:{approval_digest}",
                )
                outcomes.append(
                    {
                        "channel": "xhs",
                        "status": "queued",
                        "message": "小红书内容自动填充任务已进入队列。",
                        "external_id": None,
                        "external_url": None,
                    }
                )
                continue
            try:
                outcome = publish_package(
                    self.settings, channel=channel, paper=paper, package=output
                )
            except Exception as exc:
                self.storage.save_publication(
                    paper_id, channel, "failed", message=str(exc)
                )
                outcomes.append(
                    {
                        "channel": channel,
                        "status": "failed",
                        "message": str(exc),
                        "external_id": None,
                        "external_url": None,
                    }
                )
                continue
            self.storage.save_publication(
                paper_id,
                channel,
                outcome.status,
                external_id=outcome.external_id,
                external_url=outcome.external_url,
                message=outcome.message,
            )
            outcomes.append(outcome.to_dict())
        if outcomes and all(item["status"] == "published" for item in outcomes):
            current = self.storage.get_paper(paper_id)
            if current and current["status"] == "approved":
                self.storage.transition(
                    paper_id,
                    "published",
                    detail="all configured publication connectors confirmed success",
                )
        self.storage.log_event(
            "publication_package_ready", paper_id=paper_id, detail=f"reviewer={reviewer}"
        )
        return {
            "paper_id": paper_id,
            "status": self.storage.get_paper(paper_id)["status"],
            "download_url": f"/api/artifacts/{paper_id}/{output.name}",
            "message": "审核已通过，发布包已经准备好。",
            "outcomes": outcomes,
            "publication_job": publication_job,
        }

    def confirm_published(self, paper_id: int) -> dict[str, Any]:
        paper = self.storage.get_paper(paper_id)
        if not paper:
            raise KeyError("没有找到这篇论文。")
        if paper["status"] != "approved":
            raise ValueError("只有已批准的内容才能确认发布。")
        self.storage.transition(
            paper_id, "published", detail="platform publication confirmed in console"
        )
        for publication in self.storage.list_publications(paper_id):
            self.storage.save_publication(
                paper_id,
                publication["channel"],
                "published",
                external_id=publication["external_id"],
                external_url=publication["external_url"],
                message="用户已在控制台确认平台发布完成。",
            )
        self.storage.log_event(
            "platform_publication_confirmed", paper_id=paper_id, detail="user feedback"
        )
        return {"paper_id": paper_id, "status": "published"}

    def artifact(self, paper_id: int, filename: str) -> Path:
        if Path(filename).name != filename:
            raise ValueError("文件名不合法。")
        target = self._artifact_dir(paper_id)
        if not target:
            raise KeyError("没有找到内容包。")
        path = (target / filename).resolve()
        if path.parent != target.resolve() or not path.is_file():
            raise KeyError("没有找到文件。")
        if path.suffix.lower() not in {".png", ".zip", ".json", ".md", ".html"}:
            raise ValueError("不允许下载该文件类型。")
        return path


class ConsoleHandler(BaseHTTPRequestHandler):
    service: ConsoleService
    web_root: Path

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[console] {self.address_string()} {format % args}")

    def _json(self, value: Any, status: int = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000:
            raise ValueError("请求内容过大。")
        if not length:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("请求必须是 JSON 对象。")
        return value

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/api/overview":
                return self._json(self.service.overview())
            if path == "/api/papers":
                return self._json(self.service.list_papers(
                    query.get("q", [""])[0], query.get("status", [""])[0]
                ))
            if path == "/api/search/arxiv":
                return self._json(self.service.search_remote(query.get("q", [""])[0]))
            match = re.fullmatch(r"/api/papers/(\d+)", path)
            if match:
                return self._json(self.service.paper_detail(int(match.group(1))))
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{12})", path)
            if match:
                return self._json(self.service.get_job(match.group(1)))
            match = re.fullmatch(r"/api/artifacts/(\d+)/([^/]+)", path)
            if match:
                return self._file(self.service.artifact(int(match.group(1)), match.group(2)))
            if path.startswith("/api/"):
                return self._error(HTTPStatus.NOT_FOUND, "接口不存在。")
            return self._static(path)
        except KeyError as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc.args[0]))
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务暂时不可用：{exc}")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/api/papers/import":
                return self._json(self.service.import_paper(body), HTTPStatus.CREATED)
            if path == "/api/runs":
                return self._json(
                    self.service.run_discovery(
                        demo=bool(body.get("demo", False)),
                        select_count=int(body.get("select_count", 1)),
                    ),
                    HTTPStatus.ACCEPTED,
                )
            match = re.fullmatch(r"/api/papers/(\d+)/generate", path)
            if match:
                return self._json(
                    self.service.generate(int(match.group(1))), HTTPStatus.ACCEPTED
                )
            match = re.fullmatch(r"/api/papers/(\d+)/venue", path)
            if match:
                return self._json(self.service.configure_venue(int(match.group(1)), body))
            match = re.fullmatch(r"/api/papers/(\d+)/publish-package", path)
            if match:
                result = self.service.prepare_publication(int(match.group(1)), body)
                return self._json(
                    result,
                    HTTPStatus.ACCEPTED if result.get("publication_job") else HTTPStatus.OK,
                )
            match = re.fullmatch(r"/api/papers/(\d+)/confirm-published", path)
            if match:
                return self._json(self.service.confirm_published(int(match.group(1))))
            self._error(HTTPStatus.NOT_FOUND, "接口不存在。")
        except KeyError as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc.args[0]))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务暂时不可用：{exc}")

    def _static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        path = (self.web_root / relative).resolve()
        if path.parent != self.web_root.resolve() or not path.is_file():
            path = self.web_root / "index.html"
        self._file(path, cache=False)

    def _file(self, path: Path, *, cache: bool = True) -> None:
        payload = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "public, max-age=300" if cache else "no-cache")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        if path.suffix.lower() == ".zip":
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(payload)


def serve_console(settings: Settings, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    service = ConsoleService(settings)
    handler = type(
        "BoundConsoleHandler",
        (ConsoleHandler,),
        {"service": service, "web_root": settings.root / "web"},
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    print(f"XHS Agent Console: http://{host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
        server.server_close()
