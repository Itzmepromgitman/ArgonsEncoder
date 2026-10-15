"""Shared helpers for Tier-0 UI snapshot tests and the UI audit script."""

import json
from pathlib import Path

from plugins.settings import normalize_settings, settings_menu_card

SNAPSHOT_DIR = Path(__file__).parent / "ui_snapshots"


def serialize_markup(markup):
    """Turn an InlineKeyboardMarkup into plain JSON-able rows of cells."""
    rows = []
    for row in markup.inline_keyboard:
        cells = []
        for button in row:
            cell = {"text": button.text}
            callback_data = getattr(button, "callback_data", None)
            url = getattr(button, "url", None)
            if callback_data:
                cell["callback_data"] = callback_data
            if url:
                cell["url"] = url
            cells.append(cell)
        rows.append(cells)
    return rows


def build_surface(name):
    """Build a UI surface as ``(text, rows)`` where rows are serialized cells."""
    if name == "settings_menu":
        text, markup = settings_menu_card(normalize_settings({}))
        return text, serialize_markup(markup)
    raise KeyError(f"Unknown UI surface: {name}")


SURFACES = ("settings_menu",)


def load_golden(name):
    return json.loads((SNAPSHOT_DIR / f"{name}.json").read_text(encoding="utf-8"))


def dump_golden(name, payload):
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    (SNAPSHOT_DIR / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
