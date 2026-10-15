# Developed by ARGON telegram: @REACTIVEARGON
"""Unified progress-card skeleton for every transfer and encoding surface.

One grammar for all progress, so a download, an upload and an encode look like
the same product:

    <headline: icon + title (+ step info)>
    <stage chips: Queued > Downloading > Encoding > Delivering > Done>
    <optional subtitle, e.g. the filename>
    <blockquote>
      bar + percent
      size line
      timing line (speed . elapsed . ETA)
      extra metric lines (fps, bitrate, ...)
    </blockquote>
    <optional footer blocks (codec summary, job id, ...)>
"""
from __future__ import annotations

from typing import Iterable, Sequence

from bot.utils.format import humanbytes
from bot.utils.ui import progress_bar

STAGES: tuple = ("Queued", "Downloading", "Encoding", "Delivering", "Done")

_DONE = "✅"
_ACTIVE = "▶️"
_PENDING = "○"
_SEP = " › "


def stage_chips(stage: str, stages: Sequence[str] = STAGES) -> str:
    """Render the pipeline as chips, marking everything before ``stage`` done."""
    positions = list(stages)
    if stage not in positions:
        return ""
    index = positions.index(stage)
    chips = []
    for position, name in enumerate(positions):
        if position < index:
            chips.append(f"{_DONE}{name}")
        elif position == index:
            chips.append(f"{_ACTIVE}{name}")
        else:
            chips.append(f"{_PENDING}{name}")
    return _SEP.join(chips)


def headline(percent: float) -> str:
    percent = max(0.0, min(100.0, float(percent)))
    return f"<code>{progress_bar(percent, width=20)}</code> <b>{percent:.1f}%</b>"


def size_line(current: int, total: int, note: str = "") -> str:
    return f"📦 {humanbytes(current)} / {humanbytes(total)}{note}"


def timing_line(speed: str, elapsed: str, eta: str) -> str:
    return f"⚡ {speed} · ⏱ {elapsed} · ⏳ ETA {eta}"


def progress_block(
    percent: float,
    size_text: str,
    timing_text: str = "",
    extra_lines: Iterable[str] = (),
) -> str:
    lines = [headline(percent), size_text]
    if timing_text:
        lines.append(timing_text)
    lines.extend(line for line in extra_lines if line)
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


def card(
    title: str,
    percent: float,
    size_text: str,
    timing_text: str = "",
    *,
    stage: str = "",
    subtitle: str = "",
    step_info: str = "",
    extra_lines: Iterable[str] = (),
    footer_lines: Iterable[str] = (),
) -> str:
    """Assemble a progress card from the shared skeleton."""
    parts = [f"<b>{title}</b>{step_info}"]
    if stage:
        chips = stage_chips(stage)
        if chips:
            parts.append(chips)
    if subtitle:
        parts.append(subtitle)
    parts.append(progress_block(percent, size_text, timing_text, extra_lines))
    parts.extend(line for line in footer_lines if line)
    return "\n".join(parts)


def transfer_stage(title: str) -> str:
    """Map a transfer title like "Downloading..."/"Uploading 720p..." to a stage."""
    lowered = title.lower()
    if "download" in lowered:
        return "Downloading"
    if "upload" in lowered:
        return "Delivering"
    return ""
