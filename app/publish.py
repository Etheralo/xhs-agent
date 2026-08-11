from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import Settings


CHANNEL_LABELS = {"xhs": "小红书", "wechat": "微信公众号"}


@dataclass(slots=True)
class PublishResult:
    channel: str
    status: str
    message: str
    external_id: str | None = None
    external_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def configured_publishers(settings: Settings) -> dict[str, dict[str, Any]]:
    return {
        "xhs": {
            "label": CHANNEL_LABELS["xhs"],
            "connected": bool(settings.xhs_publish_webhook_url),
            "mode": "webhook" if settings.xhs_publish_webhook_url else "manual",
        },
        "wechat": {
            "label": CHANNEL_LABELS["wechat"],
            "connected": bool(settings.wechat_publish_webhook_url),
            "mode": "webhook" if settings.wechat_publish_webhook_url else "manual",
        },
    }


def _webhook_url(settings: Settings, channel: str) -> str | None:
    if channel == "xhs":
        return settings.xhs_publish_webhook_url
    if channel == "wechat":
        return settings.wechat_publish_webhook_url
    raise ValueError(f"Unsupported publication channel: {channel}")


def publish_package(
    settings: Settings,
    *,
    channel: str,
    paper: dict[str, Any],
    package: Path,
) -> PublishResult:
    """Deliver an approved package to a user-owned publishing connector."""
    url = _webhook_url(settings, channel)
    if not url:
        return PublishResult(
            channel=channel,
            status="manual_ready",
            message=f"{CHANNEL_LABELS[channel]}未配置发布连接器，请下载发布包后人工发布。",
        )
    metadata = {
        "schema_version": "1",
        "channel": channel,
        "paper": {
            "id": paper["id"],
            "arxiv_id": paper["arxiv_id"],
            "title": paper["title"],
        },
        "human_approved": True,
    }
    headers = {}
    if settings.publish_webhook_token:
        headers["Authorization"] = f"Bearer {settings.publish_webhook_token}"
    with package.open("rb") as handle:
        response = httpx.post(
            url,
            headers=headers,
            data={"metadata": json.dumps(metadata, ensure_ascii=False)},
            files={"package": (package.name, handle, "application/zip")},
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
        )
    response.raise_for_status()
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        payload = {}
    status = str(payload.get("status") or "delivered")
    if status not in {"delivered", "submitted", "published"}:
        status = "delivered"
    return PublishResult(
        channel=channel,
        status=status,
        message=str(payload.get("message") or f"发布包已发送至{CHANNEL_LABELS[channel]}连接器。"),
        external_id=str(payload["external_id"]) if payload.get("external_id") else None,
        external_url=str(payload["external_url"]) if payload.get("external_url") else None,
    )
