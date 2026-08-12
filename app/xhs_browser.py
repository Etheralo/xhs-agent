from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable
from urllib.request import urlopen

from .artifacts import publication_image_names
from .config import Settings
from .publish import PublishResult


Progress = Callable[[str, str, str | None], None]


class XHSLoginRequired(RuntimeError):
    """The dedicated creator profile does not currently have a valid login."""


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "缺少 Playwright。请先运行 `uv sync --dev`，再运行 "
            "`uv run playwright install chrome`。"
        ) from exc
    return sync_playwright


def split_xhs_copy(caption: str, fallback_title: str) -> tuple[str, str]:
    lines = [line.rstrip() for line in caption.strip().splitlines()]
    first_index = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_index is None:
        raise ValueError("小红书正文为空，不能发布。")
    first_line = lines[first_index].strip().lstrip("# ")
    title = (first_line or fallback_title.strip()).strip()[:20]
    # The first non-empty line is the platform title, so do not repeat it at the
    # beginning of the body. Keep it as a fallback for a title-only caption.
    body = "\n".join(lines[first_index + 1 :]).strip() or first_line
    return title, body


def load_xhs_copy(artifact_dir: Path, fallback_title: str) -> tuple[str, str]:
    """Load separate title/body artifacts, with support for legacy captions."""
    caption_path = artifact_dir / "xhs-caption.md"
    if not caption_path.is_file():
        raise ValueError("内容包缺少 xhs-caption.md。")
    caption = caption_path.read_text(encoding="utf-8")
    title_path = artifact_dir / "xhs-title.txt"
    if title_path.is_file():
        title = title_path.read_text(encoding="utf-8").strip()
        body = caption.strip()
        if not title or not body:
            raise ValueError("小红书标题或正文为空，不能填充。")
        return title, body
    return split_xhs_copy(caption, fallback_title)


def _normalized_editor_text(value: Any) -> str:
    """Normalize browser/editor-only whitespace without hiding lost content."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[\t \u00a0]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _chrome_executable(settings: Settings) -> Path:
    """Resolve a system browser that can remain open after Playwright disconnects."""
    channel = (settings.xhs_browser_channel or "chrome").lower()
    candidates: list[Path] = []
    if sys.platform == "darwin":
        applications = {
            "chrome": "Google Chrome.app/Contents/MacOS/Google Chrome",
            "chrome-beta": "Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
            "msedge": "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        }
        app = applications.get(channel)
        if app:
            candidates.extend(
                [Path("/Applications") / app, Path.home() / "Applications" / app]
            )
    elif os.name == "nt":
        programs = {
            "chrome": ("Google", "Chrome", "Application", "chrome.exe"),
            "msedge": ("Microsoft", "Edge", "Application", "msedge.exe"),
        }
        parts = programs.get(channel)
        if parts:
            for root_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
                root = os.environ.get(root_name)
                if root:
                    candidates.append(Path(root).joinpath(*parts))
    command_names = {
        "chrome": ["google-chrome", "google-chrome-stable", "chrome"],
        "chrome-beta": ["google-chrome-beta"],
        "msedge": ["microsoft-edge", "msedge"],
        "chromium": ["chromium", "chromium-browser"],
    }.get(channel, [channel])
    for command in command_names:
        resolved = shutil.which(command)
        if resolved:
            candidates.append(Path(resolved))
    executable = next((path for path in candidates if path.is_file()), None)
    if executable is None:
        raise RuntimeError(
            f"没有找到浏览器频道 {channel!r}。请安装 Chrome，或设置 "
            "XHS_BROWSER_CHANNEL=chrome。"
        )
    return executable


def _devtools_endpoint(profile_dir: Path) -> str | None:
    port_file = profile_dir / "DevToolsActivePort"
    try:
        port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
        endpoint = f"http://127.0.0.1:{port}"
        with urlopen(f"{endpoint}/json/version", timeout=0.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return endpoint if payload.get("webSocketDebuggerUrl") else None
    except (OSError, ValueError, IndexError, json.JSONDecodeError):
        return None


def _active_profile_pid(profile_dir: Path) -> int | None:
    """Return the PID holding Chrome's profile lock, ignoring stale locks."""
    try:
        lock_target = (profile_dir / "SingletonLock").readlink().name
        match = re.search(r"-(\d+)$", lock_target)
        if match is None:
            return None
        pid = int(match.group(1))
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def _connect_fill_context(playwright: Any, settings: Settings, *, timeout_ms: int):
    """Connect to an externally owned Chrome so the filled page stays open."""
    if settings.xhs_browser_headless:
        raise ValueError("人工发布模式要求 XHS_BROWSER_HEADLESS=false。")
    settings.xhs_browser_profile_dir.mkdir(parents=True, exist_ok=True)
    endpoint = _devtools_endpoint(settings.xhs_browser_profile_dir)
    if endpoint is None:
        active_pid = _active_profile_pid(settings.xhs_browser_profile_dir)
        if active_pid is not None:
            raise RuntimeError(
                "专用 Chrome 正由旧会话占用，且该会话不能被自动化连接"
                f"（PID {active_pid}）。请关闭这个使用 xhs-browser-profile 的专用 "
                "Chrome 后重试；不需要关闭日常 Chrome。"
            )
        port_file = settings.xhs_browser_profile_dir / "DevToolsActivePort"
        port_file.unlink(missing_ok=True)
        executable = _chrome_executable(settings)
        subprocess.Popen(
            [
                str(executable),
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                f"--user-data-dir={settings.xhs_browser_profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-session-crashed-bubble",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + min(timeout_ms / 1000, 20)
        while time.monotonic() < deadline:
            endpoint = _devtools_endpoint(settings.xhs_browser_profile_dir)
            if endpoint:
                break
            time.sleep(0.1)
    if endpoint is None:
        raise RuntimeError(
            "Chrome 已启动，但无法建立本地调试连接。请关闭专用 Chrome 后重试。"
        )
    browser = playwright.chromium.connect_over_cdp(endpoint, timeout=timeout_ms)
    if not browser.contexts:
        raise RuntimeError("已连接 Chrome，但没有找到可用的浏览器上下文。")
    return browser.contexts[0]


def _open_work_page(context: Any):
    reusable = next(
        (
            item
            for item in context.pages
            if item.url in {"about:blank", "chrome://newtab/", "chrome://new-tab-page/"}
        ),
        None,
    )
    return reusable or context.new_page()


def _visible(page: Any, selectors: list[str], *, timeout_ms: int = 8_000):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for selector in selectors:
            try:
                matches = page.locator(selector)
                for index in range(matches.count()):
                    locator = matches.nth(index)
                    if locator.get_attribute("aria-hidden") == "true":
                        continue
                    is_click_target = locator.evaluate(
                        """element => {
                            const rect = element.getBoundingClientRect();
                            const inViewport = rect.bottom > 0 && rect.right > 0
                                && rect.top < window.innerHeight
                                && rect.left < window.innerWidth;
                            if (!inViewport) return false;
                            const x = Math.max(0, Math.min(
                                window.innerWidth - 1,
                                rect.left + rect.width / 2
                            ));
                            const y = Math.max(0, Math.min(
                                window.innerHeight - 1,
                                rect.top + rect.height / 2
                            ));
                            const hit = element.ownerDocument.elementFromPoint(x, y);
                            return Boolean(hit && (hit === element || element.contains(hit)));
                        }"""
                    )
                    if locator.is_visible() and is_click_target:
                        return locator
            except Exception:
                continue
        page.wait_for_timeout(200)
    return None


def _present(page: Any, selector: str, *, timeout_ms: int = 8_000):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        locator = page.locator(selector).first
        try:
            if locator.count():
                return locator
        except Exception:
            pass
        page.wait_for_timeout(200)
    return None


def _image_upload_input(page: Any, *, timeout_ms: int):
    """Open the image-post panel only when its file input is not ready yet."""
    image_tab = _visible(
        page,
        [
            ".creator-tab:has-text('上传图文')",
            ".creator-tab:has-text('发布图文')",
            "[role='tab']:has-text('上传图文')",
            "[role='tab']:has-text('发布图文')",
        ],
        timeout_ms=min(timeout_ms, 5_000),
    )
    if image_tab is not None:
        try:
            classes = (image_tab.get_attribute("class") or "").split()
            aria_selected = image_tab.get_attribute("aria-selected")
        except Exception:
            classes = []
            aria_selected = None
        if "active" not in classes and aria_selected != "true":
            image_tab.click(timeout=min(timeout_ms, 10_000))
            _visible(
                page,
                [
                    ".creator-tab.active:has-text('上传图文')",
                    ".creator-tab.active:has-text('发布图文')",
                    "[role='tab'][aria-selected='true']:has-text('上传图文')",
                    "[role='tab'][aria-selected='true']:has-text('发布图文')",
                ],
                timeout_ms=min(timeout_ms, 5_000),
            )

    upload = _present(
        page,
        ", ".join(
            [
                "input[type='file'][accept*='image/']",
                "input[type='file'][accept*='.jpg']",
                "input[type='file'][accept*='.jpeg']",
                "input[type='file'][accept*='.png']",
                "input[type='file'][accept*='.webp']",
            ]
        ),
        timeout_ms=timeout_ms,
    )
    if upload is None:
        raise RuntimeError("没有找到小红书图片上传控件，页面结构可能已经变化。")
    return upload


def _login_required(page: Any) -> bool:
    if "/login" in page.url:
        return True
    return _visible(
        page,
        [
            "input[placeholder*='手机号']",
            "input[placeholder*='验证码']",
            "text=手机号登录",
        ],
        timeout_ms=1_500,
    ) is not None


def _fill(locator: Any, value: str, field_name: str) -> None:
    try:
        # Resolve the locator before filling. Some Xiaohongshu editor selectors
        # depend on the empty-state placeholder, which disappears as soon as
        # text is entered. Re-evaluating that locator afterwards would wait for
        # an element that no longer matches even though the fill succeeded.
        element = locator.element_handle()
        if element is None:
            raise RuntimeError("输入框元素已失效")
        element.click()
        element.fill(value)
        actual = element.evaluate(
            "element => 'value' in element ? element.value : element.innerText"
        )
        if _normalized_editor_text(actual) != _normalized_editor_text(value):
            raise RuntimeError("页面没有保留已填写的文本")
    except Exception as exc:
        raise RuntimeError(f"无法填写小红书{field_name}输入框：{exc}") from exc


def _diagnostic_screenshot(page: Any, settings: Settings, paper_id: int) -> Path | None:
    target = settings.output_dir / "playwright"
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"xhs-publish-{paper_id}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        return None
    return path


def publish_to_xhs(
    settings: Settings,
    *,
    paper: dict[str, Any],
    artifact_dir: Path,
    progress: Progress,
) -> PublishResult:
    """Fill one approved note and leave final publication to the user."""
    image_paths = [artifact_dir / name for name in publication_image_names(artifact_dir)]
    if len(image_paths) != 6 or any(not path.is_file() for path in image_paths):
        raise ValueError("自动填充要求内容包中恰好存在 6 张小红书配图。")
    title, body = load_xhs_copy(
        artifact_dir, str(paper.get("title") or "论文解读")
    )
    timeout_ms = max(30, settings.xhs_publish_timeout_seconds) * 1000
    sync_playwright = _playwright()

    progress("launching", "正在启动小红书内容填充浏览器", None)
    with sync_playwright() as playwright:
        context = _connect_fill_context(playwright, settings, timeout_ms=timeout_ms)
        page = _open_work_page(context)
        try:
            page.set_default_timeout(min(timeout_ms, 30_000))
            page.goto(settings.xhs_creator_url, wait_until="domcontentloaded", timeout=timeout_ms)
            if _login_required(page):
                progress("needs_login", "小红书登录已失效，请先运行 xhs-login", None)
                raise XHSLoginRequired(
                    "小红书专用浏览器尚未登录或登录已失效。请运行 "
                    "`uv run xhs-agent xhs-login` 完成短信登录后重试。"
                )

            progress("uploading", "正在上传 6 张论文页面", None)
            upload = _image_upload_input(page, timeout_ms=20_000)
            upload.set_input_files([str(path) for path in image_paths])

            progress("filling", "正在填写标题与完整推文正文", body[:500])
            title_input = _visible(
                page,
                [
                    "input[placeholder*='填写标题']",
                    "input[placeholder*='标题']",
                    "textarea[placeholder*='标题']",
                ],
                timeout_ms=timeout_ms,
            )
            body_input = _visible(
                page,
                [
                    "[contenteditable='true'][data-placeholder*='正文']",
                    "[contenteditable='true'][data-placeholder*='描述']",
                    "[contenteditable='true'][role='textbox']:has("
                    "[data-placeholder*='正文'])",
                    "[contenteditable='true'][role='textbox']:has("
                    "[data-placeholder*='描述'])",
                    ".tiptap.ProseMirror[contenteditable='true'][role='textbox']",
                    ".ql-editor[contenteditable='true']",
                    "textarea[placeholder*='正文']",
                    "textarea[placeholder*='描述']",
                ],
                timeout_ms=20_000,
            )
            if title_input is None or body_input is None:
                raise RuntimeError("没有找到小红书标题或正文输入框，页面结构可能已经变化。")
            _fill(title_input, title, "标题")
            _fill(body_input, body, "正文")
            progress(
                "filled",
                "图片、标题和正文已填入；请人工核对并点击发布，再回控制台确认结果",
                body[:500],
            )
            return PublishResult(
                channel="xhs",
                status="filled",
                message="内容已填入小红书创作者平台，等待用户手动发布并反馈结果。",
            )
        except XHSLoginRequired:
            _diagnostic_screenshot(page, settings, int(paper["id"]))
            raise
        except Exception as exc:
            screenshot = _diagnostic_screenshot(page, settings, int(paper["id"]))
            suffix = f" 诊断截图：{screenshot}" if screenshot else ""
            raise RuntimeError(f"小红书内容自动填充未完成：{exc}{suffix}") from exc


def open_xhs_login(settings: Settings) -> None:
    """Open the dedicated creator profile and keep it alive for manual login."""
    sync_playwright = _playwright()
    timeout_ms = max(30, settings.xhs_publish_timeout_seconds) * 1000
    with sync_playwright() as playwright:
        context = _connect_fill_context(playwright, settings, timeout_ms=timeout_ms)
        page = _open_work_page(context)
        page.goto(
            settings.xhs_creator_url,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        print(f"已打开小红书创作者平台：{page.url}")
        print("请在专用浏览器中完成短信登录，确认进入发布页面后回到终端按 Enter。")
        input()
        if _login_required(page):
            raise XHSLoginRequired("仍处于登录页面，登录态没有保存。")
        print(f"登录态已保存到：{settings.xhs_browser_profile_dir}")
        print("专用浏览器将保持打开，可继续用于内容填充和人工发布。")
