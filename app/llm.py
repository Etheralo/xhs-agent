from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from .config import Settings


LLMEventCallback = Callable[[str, dict[str, Any]], None]


def _chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _decode_json_content(content: Any) -> dict[str, Any]:
    if not isinstance(content, str):
        raise TypeError("模型响应中的 message.content 必须是字符串。")
    cleaned = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.I | re.S)
    if fenced:
        cleaned = fenced.group(1)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "模型返回的内容不是有效 JSON；请确认接口支持 JSON mode，或检查模型输出限制。"
        ) from exc
    if not isinstance(value, dict):
        raise TypeError("模型必须返回一个 JSON 对象。")
    return value


def _emit(callback: LLMEventCallback | None, event: str, detail: dict[str, Any]) -> None:
    if callback is None:
        return
    try:
        callback(event, detail)
    except Exception:
        # UI/telemetry callbacks must never turn a successful model request into a failure.
        return


def _public_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in diagnostics.items() if key != "started_monotonic"
    }


def _request_id(response: httpx.Response) -> str | None:
    for name in ("x-request-id", "request-id", "x-moonshot-request-id"):
        if value := response.headers.get(name):
            return value
    return None


def _usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): int(item)
        for key, item in value.items()
        if isinstance(item, int) and not isinstance(item, bool)
    }


def _delta_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
        elif isinstance(text, dict) and isinstance(text.get("value"), str):
            parts.append(text["value"])
    return "".join(parts)


def _is_kimi_thinking_model(settings: Settings) -> bool:
    return settings.llm_model.lower().startswith("kimi-")


def _payload(settings: Settings, prompt: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": settings.llm_stream,
        "max_completion_tokens": settings.llm_max_completion_tokens,
    }
    if settings.llm_json_mode:
        payload["response_format"] = {"type": "json_object"}
    # `thinking` is not part of the generic OpenAI schema, so only send it to
    # Kimi models that implement this extension.
    if settings.llm_disable_thinking and _is_kimi_thinking_model(settings):
        payload["thinking"] = {"type": "disabled"}
    return payload


def _stream_content(
    response: httpx.Response,
    callback: LLMEventCallback | None,
    diagnostics: dict[str, Any],
) -> tuple[str, str | None, dict[str, int]]:
    parts: list[str] = []
    finish_reason: str | None = None
    usage: dict[str, int] = {}
    first_chunk_at: float | None = None
    last_notified_chars = 0
    received_chars = 0
    for line in response.iter_lines():
        line = line.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError("模型流式响应包含无法解析的数据。") from exc
        if chunk_usage := _usage(chunk.get("usage")):
            usage = chunk_usage
        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        if choice.get("finish_reason") is not None:
            finish_reason = str(choice["finish_reason"])
        delta = choice.get("delta") or {}
        text = _delta_text(delta.get("content"))
        if not text:
            continue
        if first_chunk_at is None:
            first_chunk_at = time.monotonic()
            diagnostics["first_chunk_ms"] = round(
                (first_chunk_at - diagnostics["started_monotonic"]) * 1000
            )
        parts.append(text)
        received_chars += len(text)
        if received_chars - last_notified_chars >= 256:
            last_notified_chars = received_chars
            _emit(
                callback,
                "chunk",
                {**_public_diagnostics(diagnostics), "received_chars": received_chars},
            )
    content = "".join(parts)
    if content and len(content) != last_notified_chars:
        _emit(
            callback,
            "chunk",
            {**_public_diagnostics(diagnostics), "received_chars": len(content)},
        )
    return content, finish_reason, usage


def chat_json(
    settings: Settings,
    prompt: str,
    *,
    timeout: int | None = None,
    on_event: LLMEventCallback | None = None,
) -> dict[str, Any]:
    """Call an OpenAI-compatible chat completion without automatic retries."""
    started = time.monotonic()
    diagnostics: dict[str, Any] = {
        "model": settings.llm_model,
        "input_chars": len(prompt),
        "stream": settings.llm_stream,
        "started_monotonic": started,
    }
    _emit(on_event, "started", _public_diagnostics(diagnostics))
    read_timeout = timeout or settings.llm_read_timeout_seconds
    client_timeout = httpx.Timeout(
        connect=min(20, read_timeout), read=read_timeout, write=60, pool=20
    )
    try:
        with httpx.Client(timeout=client_timeout) as client:
            with client.stream(
                "POST",
                _chat_completions_url(settings.llm_base_url),
                headers={
                    "Authorization": f"Bearer {settings.llm_api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream" if settings.llm_stream else "application/json",
                },
                json=_payload(settings, prompt),
            ) as response:
                response.raise_for_status()
                diagnostics["request_id"] = _request_id(response)
                if settings.llm_stream:
                    content, finish_reason, usage = _stream_content(
                        response, on_event, diagnostics
                    )
                else:
                    body = response.json()
                    choice = body["choices"][0]
                    content = _delta_text(choice["message"]["content"])
                    finish_reason = choice.get("finish_reason")
                    usage = _usage(body.get("usage"))
        if not content.strip():
            raise ValueError("模型响应完成，但没有返回可解析的正文。")
        if finish_reason in {"length", "max_tokens"}:
            raise ValueError(
                "模型输出达到长度上限，JSON 可能不完整；请提高 LLM_MAX_COMPLETION_TOKENS。"
            )
        result = _decode_json_content(content)
        completed = _public_diagnostics(diagnostics)
        completed.update(
            duration_ms=round((time.monotonic() - started) * 1000),
            received_chars=len(content),
            finish_reason=finish_reason,
            usage=usage,
        )
        _emit(on_event, "completed", completed)
        return result
    except Exception as exc:
        failed = _public_diagnostics(diagnostics)
        failed.update(
            duration_ms=round((time.monotonic() - started) * 1000),
            error_type=type(exc).__name__,
            error=str(exc)[:500],
        )
        _emit(on_event, "failed", failed)
        raise
