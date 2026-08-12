from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .artifacts import create_approval_manifest, export_zip
from .config import load_settings
from .pipeline import Pipeline
from .storage import Storage
from .web import REVIEW_CHECKLIST, serve_console
from .xhs_browser import open_xhs_login


def _identifier(value: str) -> int | str:
    return int(value) if value.isdigit() else value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xhs-agent", description="AI Safety paper content agent"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="initialize the SQLite database")
    run = sub.add_parser("run", help="discover papers and create review packages")
    run.add_argument("--demo", action="store_true", help="use three constructed offline papers")
    run.add_argument("--fixture", type=Path, help="use a local JSON fixture")
    run.add_argument("--select-count", type=int)
    listing = sub.add_parser("list", help="list papers")
    listing.add_argument("--status")
    sub.add_parser("review", help="show the human review queue and checklist")
    show = sub.add_parser("show", help="show one paper")
    show.add_argument("paper")
    approve = sub.add_parser("approve", help="human-approve a validated package")
    approve.add_argument("paper")
    approve.add_argument("--reviewer", required=True)
    export = sub.add_parser("export", help="zip an approved package")
    export.add_argument("paper")
    published = sub.add_parser("mark-published", help="record a completed manual publication")
    published.add_argument("paper")
    serve = sub.add_parser("serve", help="start the local editorial console")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    sub.add_parser("xhs-login", help="open the dedicated Xiaohongshu browser and save login state")
    return parser


def _artifact_dir(storage: Storage, settings, paper: dict) -> Path:
    # Prefer the exact path recorded by the pipeline, independent of current date.
    with storage.connect() as db:
        row = db.execute(
            "SELECT artifact_path FROM drafts WHERE paper_id=? LIMIT 1", (paper["id"],)
        ).fetchone()
    if not row:
        raise ValueError("No draft artifact exists for this paper")
    return Path(row["artifact_path"])


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = load_settings(args.root)
    settings.ensure_dirs()
    if args.command == "serve":
        serve_console(settings, host=args.host, port=args.port)
        return 0
    if args.command == "xhs-login":
        try:
            open_xhs_login(settings)
        except (RuntimeError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0
    storage = Storage(settings.db_path)
    storage.initialize()
    try:
        if args.command == "init-db":
            print(settings.db_path)
            return 0
        if args.command == "run":
            result = Pipeline(settings, storage).run(
                demo=args.demo,
                fixture=args.fixture,
                select_count=args.select_count,
            )
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            return 1 if result.errors else 0
        if args.command == "list":
            papers = storage.list_papers(args.status)
            summary = [
                {key: paper[key] for key in ("id", "arxiv_id", "title", "status", "score")}
                for paper in papers
            ]
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        if args.command == "review":
            papers = storage.list_papers("ready_for_review")
            queue = []
            for item in papers:
                queue.append(
                    {
                        "id": item["id"],
                        "arxiv_id": item["arxiv_id"],
                        "title": item["title"],
                        "score": item["score"],
                        "artifact_path": str(_artifact_dir(storage, settings, item)),
                    }
                )
            print(json.dumps(
                {
                    "queue": queue,
                    "checklist": REVIEW_CHECKLIST,
                },
                ensure_ascii=False,
                indent=2,
            ))
            return 0
        paper = storage.get_paper(_identifier(args.paper))
        if not paper:
            raise ValueError(f"Unknown paper: {args.paper}")
        if args.command == "show":
            print(json.dumps(paper, ensure_ascii=False, indent=2))
            return 0
        target = _artifact_dir(storage, settings, paper)
        if args.command == "approve":
            # Manifest first: a failed/incomplete package never changes DB state.
            if paper["status"] != "ready_for_review":
                raise ValueError(f"Paper must be ready_for_review, got {paper['status']}")
            path = create_approval_manifest(target, reviewer=args.reviewer)
            storage.approve(int(paper["id"]))
            print(path)
            return 0
        if args.command == "export":
            if paper["status"] not in {"approved", "published"}:
                raise ValueError(f"Paper must be approved, got {paper['status']}")
            print(export_zip(target))
            return 0
        if args.command == "mark-published":
            if paper["status"] != "approved":
                raise ValueError(f"Paper must be approved, got {paper['status']}")
            storage.transition(int(paper["id"]), "published", detail="manual publication confirmed")
            print(f"published: {paper['arxiv_id']}")
            return 0
    except (ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
