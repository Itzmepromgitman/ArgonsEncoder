# Developed by ARGON telegram: @REACTIVEARGON
"""Shared design system for every user-facing message.

Design rules (apply everywhere):
- One visual language: emoji icon + bold header, details inside <blockquote>,
  values in <code>, hints in <i>.
- Buttons: primary actions first row, navigation (back/close) last row.
- Prefer editing the current message (and callback toasts) over chat spam.
- Never leak raw tracebacks to users; use a Details button when technical
  detail matters.
"""
from __future__ import annotations

from html import escape
from typing import Iterable, List, Optional, Sequence, Union

from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

__all__ = [
    "btn",
    "markup",
    "back_btn",
    "close_btn",
    "refresh_btn",
    "section",
    "card",
    "kv_block",
    "hint",
    "empty_state",
    "error_card",
    "success_card",
    "safe_edit",
    "truncate",
    "progress_bar",
    "state_label",
    "ICONS",
    "STANDARD_ACTIONS",
]

class _IconMap(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


# Canonical icons — use these instead of ad-hoc emoji per file.
ICONS = _IconMap({
    "home": "🏠",
    "help": "📚",
    "settings": "⚙️",
    "stats": "📈",
    "about": "ℹ️",
    "tutorial": "🚀",
    "features": "✨",
    "encode": "🎬",
    "queue": "📋",
    "status": "📊",
    "jobs": "🗂️",
    "refresh": "🔄",
    "back": "🔙",
    "close": "❌",
    "cancel_job": "🚫",
    "pause": "⏸️",
    "resume": "▶️",
    "download": "📥",
    "upload": "📤",
    "success": "✅",
    "error": "❌",
    "warn": "⚠️",
    "info": "ℹ️",
    "empty": "📭",
    "admin": "💠",
    "watermark": "💧",
    "thumb": "🖼️",
    "video": "🎬",
    "audio": "🎵",
    "metadata": "📝",
    "trim": "✂️",
    "clock": "⏱️",
    "link": "🔗",
    "lock": "🔒",
    "profile": "🎛️",
    "reset": "↺",
    "bolt": "⚡",
    "plus": "➕",
    "minus": "➖",
    "check": "✅",
    "document": "📄",
    "user": "👤",
    "chevron_left": "⬅️",
    "chevron_right": "➡️",
})


def btn(text: str, callback_data: Optional[str] = None, url: Optional[str] = None) -> InlineKeyboardButton:
    """Single button constructor used across the bot."""
    if callback_data is not None:
        return InlineKeyboardButton(text, callback_data=callback_data)
    return InlineKeyboardButton(text, url=url or "https://t.me/")


def markup(*rows: Union[InlineKeyboardButton, Sequence[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    """Build a keyboard from rows of buttons or single buttons."""
    normalized: List[List[InlineKeyboardButton]] = []
    for row in rows:
        if isinstance(row, InlineKeyboardButton):
            normalized.append([row])
        else:
            normalized.append(list(row))
    return InlineKeyboardMarkup(normalized)


def back_btn(callback_data: str = "cb_start", label: Optional[str] = None) -> InlineKeyboardButton:
    return btn(label or f"{ICONS['back']} Back", callback_data=callback_data)


def close_btn(label: Optional[str] = None) -> InlineKeyboardButton:
    return btn(label or f"{ICONS['close']} Close", callback_data="cb_close")


def refresh_btn(callback_data: str) -> InlineKeyboardButton:
    return btn(f"{ICONS['refresh']} Refresh", callback_data=callback_data)


def section(title: str, body: str, quote: bool = True) -> str:
    """Title line + optional blockquoted body."""
    if quote and body:
        return f"{title}\n<blockquote>{body}</blockquote>"
    return f"{title}\n{body}" if body else title


def kv_block(pairs: Iterable[tuple], indent: str = "") -> str:
    """Render (label, value) pairs inside one blockquote."""
    lines = [f"{indent}{escape(str(k))}: {v}" for k, v in pairs]
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


def hint(text: str) -> str:
    return f"<i>{text}</i>"


def card(title: str, *blocks: str, footer: Optional[str] = None) -> str:
    """Assemble a full card: title, quoted blocks, optional italic footer."""
    parts = [f"<b>{title}</b>"]
    for b in blocks:
        if b:
            parts.append(b if b.startswith("<blockquote") else f"<blockquote>{b}</blockquote>")
    if footer:
        parts.append(hint(footer))
    return "\n\n".join(parts)


def empty_state(title: str, body: str, tip: Optional[str] = None) -> str:
    parts = [f"{ICONS['empty']} <b>{escape(title)}</b>"]
    if body:
        parts.append(f"<blockquote>{body}</blockquote>")
    if tip:
        parts.append(hint(tip))
    return "\n\n".join(parts)


def error_card(title: str, detail: str = "", retry: str = "", technical: bool = False) -> str:
    body_parts = []
    if detail:
        if technical:
            body_parts.append(f"<code>{escape(detail)}</code>")
        else:
            body_parts.append(escape(detail))
    text = f"{ICONS['error']} <b>{escape(title)}</b>"
    if body_parts:
        text += "\n<blockquote>" + "\n".join(body_parts) + "</blockquote>"
    if retry:
        text += "\n" + hint(retry)
    return text


def success_card(title: str, body: str = "") -> str:
    text = f"{ICONS['success']} <b>{escape(title)}</b>"
    if body:
        text += f"\n<blockquote>{body}</blockquote>"
    return text


async def safe_edit(message, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> bool:
    """Edit text tolerating identical content and missing messages."""
    try:
        await message.edit_text(text=text, reply_markup=reply_markup)
        return True
    except MessageNotModified:
        return False
    except Exception:
        return False


def truncate(text: str, limit: int = 32) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def progress_bar(percent: float, width: int = 20) -> str:
    """Render one clamped progress bar for every transfer/encoding surface."""
    percent = max(0.0, min(100.0, float(percent)))
    width = max(4, int(width))
    filled = int(percent / 100 * width)
    return "▰" * filled + "▱" * (width - filled)


def state_label(label: str, active: bool, active_icon: str = "✅") -> str:
    """Add a consistent selected-state marker to inline button labels."""
    return f"{active_icon} {label}" if active else label


# Frequently reused full rows (keep navigation placement consistent).
def STANDARD_ACTIONS(extra_rows: Optional[List[List[InlineKeyboardButton]]] = None) -> List[List[InlineKeyboardButton]]:
    rows = extra_rows or []
    return rows + [[close_btn()]]
