from __future__ import annotations

import asyncio
import html
from pathlib import Path
from typing import Any

from nonebot import get_plugin_config

from .config import Config
from .quota import (
    AccountQuota,
    PlatformQuota,
    QuotaBoard,
    QuotaWindow,
    format_reset_zh,
    platform_total_chips,
    sort_windows,
)

CARDS_PER_IMAGE = 8
_ASSETS = Path(__file__).resolve().parent / "assets"
_TEMPLATE = (_ASSETS / "quota.html").read_text(encoding="utf-8")
_CSS = (_ASSETS / "quota.css").read_text(encoding="utf-8")

_BADGES = {
    "claude": "CL",
    "codex": "CX",
    "antigravity": "AG",
    "kimi": "KM",
    "xai": "xAI",
    "gemini-cli": "GM",
    "other": "?",
}

_GROUP_TITLES = {
    "gemini": "Gemini Models",
    "claude-gpt": "Claude and GPT Models",
    "code": "Codex",
    "claude": "Claude",
    "xai": "Billing",
    "kimi": "Usage",
    "other": "Quota",
}

_lock = asyncio.Lock()
_playwright: Any = None
_browser: Any = None


class RenderError(Exception):
    """出图失败，消息可直接发给管理员。"""


def build_platform_html(
    section: PlatformQuota,
    accounts: list[AccountQuota] | None = None,
    *,
    page: int = 1,
    pages: int = 1,
    width: int | None = None,
) -> str:
    cards = accounts if accounts is not None else section.accounts
    canvas = width if width is not None else _canvas_width()
    chips = "".join(
        f'<span class="chip badge-outline">{html.escape(chip)}</span>'
        for chip in platform_total_chips(section)
    )
    grid_class = "grid single" if len(cards) == 1 else "grid"
    note = f'<p class="page-note">第 {page}/{pages} 页</p>' if pages > 1 else ""
    extra = " · 合并卡片" if len(section.accounts) > 1 else ""
    body = (
        f'<div class="sheet platform-{html.escape(section.platform)}">'
        f'<section class="card summary">'
        f'<div class="card-header"><div class="summary-head">'
        f'<span class="avatar">{html.escape(_BADGES.get(section.platform, "?"))}</span>'
        f"<div><h1 class=\"card-title\">{html.escape(section.title)}</h1>"
        f'<p class="card-description">{len(section.accounts)} 账号{extra}</p>'
        f"</div></div></div>"
        f'<div class="card-content"><div class="chips">{chips}</div></div>'
        f"</section>"
        f'<div class="{grid_class}">{"".join(_card_html(account) for account in cards)}</div>'
        f"{note}</div>"
    )
    css = _CSS.replace("__WIDTH__", str(canvas))
    return _TEMPLATE.replace("__CSS__", css).replace("__BODY__", body)


async def render_platform_images(section: PlatformQuota) -> list[bytes]:
    if not section.accounts:
        raise RenderError(f"{section.title} 没有可出图的账号。")
    pages = _chunks(section.accounts, CARDS_PER_IMAGE)
    images: list[bytes] = []
    for index, accounts in enumerate(pages, start=1):
        html_doc = build_platform_html(section, accounts, page=index, pages=len(pages))
        images.append(await _screenshot(html_doc))
    return images


async def render_board_images(board: QuotaBoard) -> list[tuple[str, list[bytes]]]:
    results: list[tuple[str, list[bytes]]] = []
    for section in board.platforms:
        results.append((section.platform, await render_platform_images(section)))
    return results


async def close_renderer() -> None:
    global _playwright, _browser
    async with _lock:
        if _browser is not None:
            try:
                await _browser.close()
            except Exception:
                pass
            _browser = None
        if _playwright is not None:
            try:
                await _playwright.stop()
            except Exception:
                pass
            _playwright = None


def _card_html(account: AccountQuota) -> str:
    flags = ""
    if account.disabled or account.cooling:
        bits = []
        if account.disabled:
            bits.append('<span class="flag flag-warn">disabled</span>')
        if account.cooling:
            bits.append('<span class="flag flag-warn">cooling</span>')
        flags = f'<div class="card-action">{"".join(bits)}</div>'
    plan = (
        f'<span class="badge badge-secondary plan">Plan: {html.escape(account.plan)}</span>'
        if account.plan
        else ""
    )
    meta = f'<div class="meta-row">{plan}</div>' if plan else ""
    head = (
        f'<div class="card-header"><div class="title-row">'
        f'<h3 class="card-title">{html.escape(account.name)}</h3>{flags}</div>'
        f"{meta}</div>"
    )
    if account.error:
        return (
            f'<article class="card">{head}'
            f'<div class="card-content"><div class="error">{html.escape(account.error)}</div></div>'
            f"</article>"
        )
    if not account.windows:
        status = html.escape(account.status or "unknown")
        return (
            f'<article class="card">{head}'
            f'<div class="card-content"><p class="empty">{status}（无上游额度）</p></div>'
            f"</article>"
        )
    groups = "".join(_group_html(title, windows) for title, windows in _grouped_windows(account.windows))
    return f'<article class="card">{head}<div class="card-content">{groups}</div></article>'


def _group_html(title: str, windows: list[QuotaWindow]) -> str:
    rows = "".join(_bar_html(window) for window in windows)
    return f'<section class="group"><h2>{html.escape(title)}</h2>{rows}</section>'


def _bar_html(window: QuotaWindow) -> str:
    remain = window.remaining_percent
    used = window.used_percent
    if remain is None and used is not None:
        remain = max(0.0, 100.0 - used)
    width = 0.0 if remain is None else max(0.0, min(100.0, remain))
    if remain is not None:
        value = f"还剩 {remain:.0f}%"
    elif used is not None:
        value = f"已用 {used:.0f}%"
    elif window.remaining is not None and window.limit is not None:
        value = f"{window.remaining:.0f}/{window.limit:.0f}"
    else:
        value = "额度可用"
    reset = ""
    zh_reset = format_reset_zh(window.reset_label)
    if zh_reset:
        reset = f'<div class="reset">{html.escape(zh_reset)}</div>'
    shift = 100.0 - width
    return (
        f'<div class="bar-row"><div class="bar-meta">'
        f'<span class="label">{html.escape(window.label)}</span>'
        f'<span class="remain">{html.escape(value)}</span></div>'
        f'<div class="progress" role="progressbar" aria-valuenow="{width:.0f}" aria-valuemin="0" aria-valuemax="100">'
        f'<div class="progress-indicator" style="transform:translateX(-{shift:.1f}%)"></div></div>'
        f"{reset}</div>"
    )


def _grouped_windows(windows: list[QuotaWindow]) -> list[tuple[str, list[QuotaWindow]]]:
    grouped: dict[str, list[QuotaWindow]] = {}
    order: list[str] = []
    for window in windows:
        key = _group_key(window.id)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(window)
    return [(_GROUP_TITLES.get(key, key), sort_windows(grouped[key])) for key in order]


def _group_key(window_id: str) -> str:
    if window_id.startswith("gemini-"):
        return "gemini"
    if window_id.startswith("claude-gpt-"):
        return "claude-gpt"
    if window_id.startswith("code-"):
        return "code"
    if window_id.startswith("grok-") or window_id == "billing":
        return "xai"
    if window_id.startswith("limit-") or window_id == "usage":
        return "kimi"
    if window_id in {
        "five_hour",
        "seven_day",
        "seven_day_opus",
        "seven_day_sonnet",
        "seven_day_oauth_apps",
        "seven_day_cowork",
        "iguana_necktie",
        "extra",
    }:
        return "claude"
    return "other"


def _chunks(items: list[AccountQuota], size: int) -> list[list[AccountQuota]]:
    return [items[index : index + size] for index in range(0, len(items), size)] or [[]]


def _canvas_width() -> int:
    try:
        return max(400, min(720, int(get_plugin_config(Config).cpa_quota_image_width)))
    except Exception:
        return 520


async def _screenshot(html_doc: str) -> bytes:
    width = _canvas_width()
    async with _lock:
        browser = await _ensure_browser()
        context = await browser.new_context(
            viewport={"width": width + 24, "height": 720},
            device_scale_factor=2,
        )
        page = await context.new_page()
        try:
            await page.set_content(html_doc, wait_until="load")
            png = await page.locator("#root").screenshot(type="png")
        except Exception as exc:
            raise RenderError(f"截图失败：{exc}") from exc
        finally:
            await context.close()
    return bytes(png)


async def _ensure_browser() -> Any:
    global _playwright, _browser
    if _browser is not None:
        try:
            if _browser.is_connected():
                return _browser
        except Exception:
            _browser = None
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RenderError("未安装 playwright。请 uv sync 后执行 playwright install chromium。") from exc
    try:
        if _playwright is None:
            _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(headless=True)
    except Exception as exc:
        message = str(exc)
        if "Executable doesn't exist" in message or "chromium" in message.lower():
            raise RenderError("未安装 Chromium。请执行：playwright install chromium") from exc
        raise RenderError(f"无法启动 Chromium：{exc}") from exc
    return _browser
