# Developed by ARGON telegram: @REACTIVEARGON
"""Exactly-once callback answering with informative-message preference.

Pyrofork answers a callback query with a single ``messages.SetBotCallbackAnswer``
call; Telegram rejects a second answer for the same query. The settings UI used
to answer an *empty* "stop the spinner" toast first and then try to answer again
with the real confirmation or error, so those messages never reached users.

``answer_once`` fixes that by deferring the empty answer until the end of a
guarded handler:

* the first *informative* answer (a confirmation or an alert) is sent
  immediately, so the spinner still stops promptly, and
* if a handler only ever answers empty, the deferred empty answer is flushed
  when the handler finishes, so a spinner can never hang.

State is keyed by ``CallbackQuery.id`` and is always removed in a ``finally``,
so an unexpected exception cannot leak memory or leave a spinner running.
"""
from __future__ import annotations

import functools
from typing import Any, Dict, Optional, Tuple

from bot.logger import LOGGER

log = LOGGER(__name__)

# Telegram caps an answerCallbackQuery notification at 200 characters.
MAX_TOAST_LEN = 200

_state: Dict[str, Dict[str, Any]] = {}


def _key(callback_query) -> Optional[str]:
    qid = getattr(callback_query, "id", None)
    return str(qid) if qid is not None else None


def _entry(callback_query) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    key = _key(callback_query)
    if key is None:
        return None, None
    entry = _state.get(key)
    if entry is None:
        entry = {"depth": 0, "sent": False, "deferred": False}
        _state[key] = entry
    return key, entry


async def _send(callback_query, text, show_alert) -> bool:
    try:
        await callback_query.answer(text, show_alert=show_alert)
        return True
    except Exception as exc:  # pragma: no cover - depends on the live client
        log.debug(f"Callback answer was no longer available: {exc}")
        return False


async def answer_once(callback_query, text: Optional[str] = None, show_alert: bool = False) -> bool:
    """Send at most one answer per callback query, preferring the informative one."""
    if text is not None:
        text = text[:MAX_TOAST_LEN]
    _, entry = _entry(callback_query)
    if entry is None:
        return await _send(callback_query, text, show_alert)

    if text or show_alert:
        if entry["sent"]:
            return False
        entry["sent"] = True
        entry["deferred"] = False
        return await _send(callback_query, text, show_alert)

    # Empty answer: stop the spinner now unless a guarded handler still runs.
    if entry["sent"] or entry["deferred"]:
        return False
    if entry["depth"] > 0:
        entry["deferred"] = True
        return False
    return await _send(callback_query, None, False)


def guard_callback_answers(handler):
    """Wrap a callback handler so a deferred empty answer is flushed on exit."""
    if getattr(handler, "__argons_answer_guard__", False):
        return handler

    @functools.wraps(handler)
    async def wrapper(client, callback_query, *args, **kwargs):
        key, entry = _entry(callback_query)
        if entry is not None:
            entry["depth"] += 1
        try:
            return await handler(client, callback_query, *args, **kwargs)
        finally:
            if entry is not None:
                entry["depth"] -= 1
                if entry["depth"] <= 0:
                    _state.pop(key, None)
                    if entry["deferred"] and not entry["sent"]:
                        await _send(callback_query, None, False)

    wrapper.__argons_answer_guard__ = True
    return wrapper
