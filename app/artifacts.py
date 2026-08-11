from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

from .models import ContentBundle, FactSheet, Paper
from .storage import now_iso


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_draft_artifacts(
    target: Path, paper: Paper, facts: FactSheet, content: ContentBundle
) -> None:
    target.mkdir(parents=True, exist_ok=True)
    source = paper.to_dict()
    # Runtime-only source text can be large and belongs in the cache, not the package.
    source.pop("metadata", None)
    write_json(target / "source.json", source)
    write_json(target / "facts.json", facts.to_dict())
    write_json(target / "xhs-slides.json", content.slides)
    (target / "xhs-caption.md").write_text(content.caption + "\n", encoding="utf-8")
    (target / "wechat.md").write_text(content.wechat_markdown + "\n", encoding="utf-8")
    (target / "wechat.html").write_text(content.wechat_html, encoding="utf-8")


def publication_image_names(target: Path) -> list[str]:
    """Return the declared publication images, with a legacy-artifact fallback."""
    manifest_path = target / "publication-images.json"
    if manifest_path.is_file():
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = raw.get("files", [])
        if not isinstance(files, list):
            return []
        return [
            name
            for item in files
            if isinstance(item, str)
            and (name := Path(item).name) == item
            and name.startswith("xhs-")
            and name.endswith(".png")
        ]
    # Legacy draft directories can contain generated cards without a manifest.
    # Treat the first six as publication images so current exports follow the rule.
    return [path.name for path in sorted(target.glob("xhs-[0-9][0-9].png"))[:6]]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_approval_manifest(target: Path, *, reviewer: str) -> Path:
    publication_images = publication_image_names(target)
    if not publication_images:
        raise ValueError("Cannot approve incomplete package: publication images")
    required = [
        "source.json", "facts.json", "validation.json", "xhs-caption.md",
        "wechat.md", "wechat.html", *publication_images,
    ]
    if (target / "publication-images.json").is_file():
        required.append("publication-images.json")
    missing = [name for name in required if not (target / name).is_file()]
    if missing:
        raise ValueError(f"Cannot approve incomplete package: {', '.join(missing)}")
    validation = json.loads((target / "validation.json").read_text(encoding="utf-8"))
    if not validation.get("passed"):
        raise ValueError("Cannot approve a package that failed validation")
    manifest = {
        "approved_at": now_iso(),
        "reviewer": reviewer,
        "human_review_required": True,
        "files": {name: _sha256(target / name) for name in required},
        "publishing": "manual",
    }
    path = target / "FINAL-APPROVED.json"
    write_json(path, manifest)
    return path


def export_zip(target: Path) -> Path:
    manifest = target / "FINAL-APPROVED.json"
    if not manifest.exists():
        raise ValueError("Only an approved package can be exported")
    output = target / f"{target.name}-approved.zip"
    declared_images = set(publication_image_names(target))
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(target.iterdir()):
            if not path.is_file() or path == output:
                continue
            if (
                path.name.startswith("xhs-")
                and path.suffix == ".png"
                and path.name not in declared_images
            ):
                continue
            archive.write(path, arcname=path.name)
    return output
