"""Shared helpers for Tier-0 UI snapshot tests and the UI audit script."""

import json
from pathlib import Path
from types import SimpleNamespace

from bot.func.editquery import progress_markup
from bot.func.encode import FFmpegProcess
from bot.utils import progress_card
from bot.utils.ui import btn, markup
from plugins.settings import (
    normalize_settings,
    presets_menu_card,
    settings_menu_card,
)

SNAPSHOT_DIR = Path(__file__).parent / "ui_snapshots"


def serialize_markup(markup_obj):
    """Turn an InlineKeyboardMarkup into plain JSON-able rows of cells."""
    rows = []
    for row in markup_obj.inline_keyboard:
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


def _fake_encode_process():
    return SimpleNamespace(
        output_file=str(Path("definitely-missing") / "clip.mp4"),
        stats=SimpleNamespace(
            percent=42.5,
            speed="2.3x",
            elapsed="00:12",
            eta="00:31",
            fps=29.97,
            bitrate="1234kb/s",
            estimated_size="57.00 MB",
        ),
        original_size=10 * 1024 * 1024,
        output_files=[{}],
        current_step=1,
        total_steps=2,
        is_paused=False,
        file_name="clip.mp4",
        codec="libx264",
        crf="23",
        preset="medium",
        resolution="1080p",
        job_id="abc123",
        estimate_line="🎯 ≈ 38–57 MB · ⏱ ≈ 2–4 min",
    )


def build_surface(name):
    """Build a UI surface as ``(text, rows)`` where rows are serialized cells."""
    if name == "settings_menu":
        text, markup_obj = settings_menu_card(normalize_settings({}))
        return text, serialize_markup(markup_obj)

    if name == "presets_menu":
        settings = normalize_settings(
            {"presets": {"My Clip": {"video": {"resolution": ["720p"], "crf": "22"}}}}
        )
        text, markup_obj = presets_menu_card(settings)
        return text, serialize_markup(markup_obj)

    if name == "encode_progress":
        process = _fake_encode_process()
        return (
            FFmpegProcess.get_progress_ui(process),
            serialize_markup(progress_markup(process)),
        )

    if name == "transfer_progress":
        text = progress_card.card(
            title="📥 Downloading...",
            percent=42.5,
            size_text=progress_card.size_line(4_456_789, 10_485_760),
            timing_text=progress_card.timing_line("1.2 MiB/s", "00:12", "00:16"),
            stage=progress_card.transfer_stage("📥 Downloading..."),
        )
        return text, serialize_markup(
            markup(btn("❌ Cancel", "cb_xfer_cancel"))
        )

    raise KeyError(f"Unknown UI surface: {name}")


SURFACES = (
    "settings_menu",
    "presets_menu",
    "encode_progress",
    "transfer_progress",
)


def load_golden(name):
    return json.loads((SNAPSHOT_DIR / f"{name}.json").read_text(encoding="utf-8"))


def dump_golden(name, payload):
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    (SNAPSHOT_DIR / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
