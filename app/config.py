from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    root: Path
    data_dir: Path
    output_dir: Path
    db_path: Path
    max_results: int
    days_back: int
    select_count: int
    require_verified_venue: bool
    request_timeout_seconds: int
    user_agent: str
    arxiv_categories: list[str]
    venues: dict[str, Any]
    topics: dict[str, Any]
    venue_overrides: dict[str, Any]
    canvas_width: int
    canvas_height: int
    xhs_slide_count: int
    wechat_min_chars: int
    wechat_max_chars: int
    font_candidates: list[str]
    llm_api_key: str | None
    llm_base_url: str
    llm_model: str
    llm_stream: bool
    llm_json_mode: bool
    llm_disable_thinking: bool
    llm_read_timeout_seconds: int
    llm_max_input_chars: int
    llm_max_completion_tokens: int
    openalex_api_key: str | None
    xhs_publish_webhook_url: str | None
    wechat_publish_webhook_url: str | None
    publish_webhook_token: str | None

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "cache").mkdir(parents=True, exist_ok=True)


def load_settings(root: Path | None = None) -> Settings:
    root = (root or Path.cwd()).resolve()
    load_dotenv(root / ".env")
    raw = _load_yaml(root / "config" / "settings.yaml")
    agent = raw.get("agent", {})
    sources = raw.get("sources", {})
    content = raw.get("content", {})
    llm = raw.get("llm", {})
    data_dir = Path(os.environ.get("XHS_AGENT_DATA_DIR", root / "data")).resolve()
    output_dir = Path(os.environ.get("XHS_AGENT_OUTPUT_DIR", root / "output")).resolve()
    return Settings(
        root=root,
        data_dir=data_dir,
        output_dir=output_dir,
        db_path=data_dir / "agent.sqlite3",
        max_results=int(agent.get("max_results", 50)),
        days_back=int(agent.get("days_back", 7)),
        select_count=int(agent.get("select_count", 1)),
        require_verified_venue=bool(agent.get("require_verified_venue", True)),
        request_timeout_seconds=int(agent.get("request_timeout_seconds", 45)),
        user_agent=str(agent.get("user_agent", "xhs-paper-agent/0.1")),
        arxiv_categories=list(sources.get("arxiv_categories", ["cs.CR", "cs.AI"])),
        venues=_load_yaml(root / "config" / "venues.yaml"),
        topics=_load_yaml(root / "config" / "topics.yaml").get("topics", {}),
        venue_overrides=_load_yaml(root / "config" / "venue_overrides.yaml"),
        canvas_width=int(content.get("canvas_width", 1242)),
        canvas_height=int(content.get("canvas_height", 1660)),
        xhs_slide_count=int(content.get("xhs_slide_count", 6)),
        wechat_min_chars=int(content.get("wechat_min_chars", 900)),
        wechat_max_chars=int(content.get("wechat_max_chars", 2400)),
        font_candidates=list(content.get("font_candidates", [])),
        # OPENAI_* names describe the protocol, not a required vendor. The
        # legacy DEEPSEEK_* fallback keeps existing local installations valid.
        llm_api_key=(
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or None
        ),
        llm_base_url=(
            os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("DEEPSEEK_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/"),
        llm_model=(
            os.environ.get("OPENAI_MODEL")
            or os.environ.get("DEEPSEEK_MODEL")
            or "your-model-name"
        ),
        llm_stream=_env_bool("LLM_STREAM", bool(llm.get("stream", True))),
        llm_json_mode=_env_bool("LLM_JSON_MODE", bool(llm.get("json_mode", True))),
        llm_disable_thinking=_env_bool(
            "LLM_DISABLE_THINKING", bool(llm.get("disable_thinking", True))
        ),
        llm_read_timeout_seconds=int(
            os.environ.get("LLM_READ_TIMEOUT_SECONDS", llm.get("read_timeout_seconds", 600))
        ),
        llm_max_input_chars=int(
            os.environ.get("LLM_MAX_INPUT_CHARS", llm.get("max_input_chars", 55_000))
        ),
        llm_max_completion_tokens=int(
            os.environ.get(
                "LLM_MAX_COMPLETION_TOKENS", llm.get("max_completion_tokens", 4096)
            )
        ),
        openalex_api_key=os.environ.get("OPENALEX_API_KEY") or None,
        xhs_publish_webhook_url=os.environ.get("XHS_PUBLISH_WEBHOOK_URL") or None,
        wechat_publish_webhook_url=os.environ.get("WECHAT_PUBLISH_WEBHOOK_URL") or None,
        publish_webhook_token=os.environ.get("PUBLISH_WEBHOOK_TOKEN") or None,
    )
