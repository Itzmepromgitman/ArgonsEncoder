"""Validated defaults and quality presets for user encoding settings.

Keeping normalization outside the Telegram handlers makes old database documents
safe to render and gives every UI surface one canonical value set.
"""
from __future__ import annotations

import copy
import re
from string import Formatter
from typing import Any, Dict

from bot.func.ffmpeg_utils import (
    VALID_AUDIO_CODECS,
    VALID_CODECS,
    VALID_PRESETS,
    validate_ffmpeg_command,
)

VALID_RESOLUTIONS = ("1080p", "720p", "480p", "360p")
VALID_PROFILES = {"fast", "balanced", "compact", "custom"}

DEFAULT_SETTINGS: Dict[str, Any] = {
    "profile": "custom",
    "video": {
        "crf": "23",
        "preset": "medium",
        "resolution": ["1080p"],
        "codec": "libx264",
        "subtitle_mode": "copy",
        "sample_seconds": 0,
        "remux": False,
        "trim_start": 0,
        "trim_end": 0,
    },
    "audio": {"bitrate": "128k", "codec": "aac", "track": "all"},
    "metadata": {
        "global": {"title": "Encoded by Argons", "author": "Argons Encoder"},
        "video": {},
        "audio": {},
        "subtitle": {},
    },
    "custom_ffmpeg": {},
    "active_custom_ffmpeg": "",
    "rename": {"pattern": ""},
    "output_as_video": False,
    "watermark": {
        "enabled": False,
        "type": "text",
        "position": "top-right",
        "opacity": 0.5,
        "text": "Argons Encoder",
        "font_size": 24,
        "border_opacity": 0.5,
        "timing_mode": "always",
        "margins": {"top": 10, "bottom": 10, "left": 10, "right": 10},
    },
    "thumbnail": None,
}

QUALITY_PRESETS: Dict[str, Dict[str, Any]] = {
    "fast": {
        "label": "⚡ Fast",
        "description": "H.264 · veryfast · 128k audio",
        "video": {"codec": "libx264", "crf": "23", "preset": "veryfast"},
        "audio": {"codec": "aac", "bitrate": "128k"},
    },
    "balanced": {
        "label": "⚖️ Balanced",
        "description": "H.264 · medium · 160k audio",
        "video": {"codec": "libx264", "crf": "22", "preset": "medium"},
        "audio": {"codec": "aac", "bitrate": "160k"},
    },
    "compact": {
        "label": "📦 Compact",
        "description": "HEVC · slow · smaller files",
        "video": {"codec": "libx265", "crf": "28", "preset": "slow"},
        "audio": {"codec": "aac", "bitrate": "128k"},
    },
}


def _bounded_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def _deep_merge(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def _valid_rename_pattern(value: str) -> bool:
    allowed = {"original", "res", "codec", "date"}
    try:
        for _, field_name, format_spec, conversion in Formatter().parse(value):
            if field_name is not None and field_name not in allowed:
                return False
            if format_spec:
                return False
            if conversion not in (None, "s", "r", "a"):
                return False
        return True
    except ValueError:
        return False


def normalize_settings(raw: Any) -> Dict[str, Any]:
    """Return a defensive, range-checked copy suitable for UI and encoding."""
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    if isinstance(raw, dict):
        _deep_merge(settings, raw)

    profile = str(settings.get("profile", "custom")).lower()
    settings["profile"] = profile if profile in VALID_PROFILES else "custom"

    video = settings["video"]
    codec = str(video.get("codec", "libx264"))
    video["codec"] = codec if codec in VALID_CODECS else "libx264"
    preset = str(video.get("preset", "medium"))
    video["preset"] = preset if preset in VALID_PRESETS else "medium"
    try:
        video["crf"] = str(max(0, min(51, int(video.get("crf", 23)))))
    except (TypeError, ValueError):
        video["crf"] = "23"

    raw_resolutions = video.get("resolution", ["1080p"])
    if isinstance(raw_resolutions, str):
        raw_resolutions = [raw_resolutions]
    if not isinstance(raw_resolutions, list):
        raw_resolutions = ["1080p"]
    resolutions = [res for res in raw_resolutions if res in VALID_RESOLUTIONS]
    video["resolution"] = list(dict.fromkeys(resolutions)) or ["1080p"]
    video["subtitle_mode"] = (
        "drop" if video.get("subtitle_mode") == "drop" else "copy"
    )
    video["remux"] = bool(video.get("remux", False))
    try:
        sample = int(video.get("sample_seconds", 0) or 0)
    except (TypeError, ValueError):
        sample = 0
    video["sample_seconds"] = sample if sample == 0 or 10 <= sample <= 600 else 0
    try:
        trim_start = max(0.0, float(video.get("trim_start", 0) or 0))
        trim_end = max(0.0, float(video.get("trim_end", 0) or 0))
    except (TypeError, ValueError):
        trim_start = trim_end = 0.0
    if trim_end <= trim_start:
        trim_start = trim_end = 0.0
    video["trim_start"] = trim_start
    video["trim_end"] = trim_end

    audio = settings["audio"]
    audio_codec = str(audio.get("codec", "aac"))
    audio["codec"] = audio_codec if audio_codec in VALID_AUDIO_CODECS else "aac"
    bitrate = str(audio.get("bitrate", "128k")).lower()
    match = re.fullmatch(r"(\d{1,4})k", bitrate)
    audio["bitrate"] = (
        bitrate
        if match and 32 <= int(match.group(1)) <= 512
        else "128k"
    )
    track = str(audio.get("track", "all")).lower()
    if track.isdigit():
        track = str(max(1, int(track)))
    audio["track"] = track if track in ("all", "none") or track.isdigit() else "all"

    metadata = settings.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    for category in ("global", "video", "audio", "subtitle"):
        values = metadata.get(category)
        if not isinstance(values, dict):
            values = {}
        metadata[category] = {
            str(key): str(value)[:500]
            for key, value in values.items()
            if isinstance(key, str) and value not in (None, "")
        }
    settings["metadata"] = metadata

    custom = settings.get("custom_ffmpeg")
    if not isinstance(custom, dict):
        custom = {}
    settings["custom_ffmpeg"] = {
        str(name): str(command)
        for name, command in custom.items()
        if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", str(name))
        and isinstance(command, str)
        and 0 < len(command) <= 1000
        and validate_ffmpeg_command(command)
    }
    active = str(settings.get("active_custom_ffmpeg", ""))
    settings["active_custom_ffmpeg"] = (
        active if active in settings["custom_ffmpeg"] else ""
    )

    rename = settings.get("rename")
    pattern = str(rename.get("pattern", "")) if isinstance(rename, dict) else ""
    pattern = pattern[:60]
    settings["rename"] = {"pattern": pattern if _valid_rename_pattern(pattern) else ""}
    settings["output_as_video"] = bool(settings.get("output_as_video", False))

    watermark = settings.get("watermark")
    if not isinstance(watermark, dict):
        watermark = {}
    watermark["enabled"] = bool(watermark.get("enabled", False))
    watermark["type"] = (
        watermark.get("type") if watermark.get("type") in ("text", "image") else "text"
    )
    watermark["position"] = (
        watermark.get("position")
        if watermark.get("position")
        in ("top-left", "top-right", "bottom-left", "bottom-right")
        else "top-right"
    )
    watermark["opacity"] = _bounded_float(watermark.get("opacity"), 0.5, 0.1, 1.0)
    watermark["border_opacity"] = _bounded_float(
        watermark.get("border_opacity"), 0.5, 0.0, 1.0
    )
    if "scale" in watermark:
        watermark["scale"] = _bounded_float(watermark.get("scale"), 0.1, 0.1, 1.0)
    watermark["text"] = str(watermark.get("text", "Argons Encoder"))[:200]
    try:
        watermark["font_size"] = max(10, min(100, int(watermark.get("font_size", 24))))
    except (TypeError, ValueError):
        watermark["font_size"] = 24
    watermark["timing_mode"] = (
        watermark.get("timing_mode")
        if watermark.get("timing_mode") in ("always", "range", "interval")
        else "always"
    )
    if watermark["timing_mode"] == "range":
        start_time = _bounded_float(watermark.get("start_time"), 0, 0, 86_400)
        end_time = _bounded_float(watermark.get("end_time"), 0, 0, 86_400)
        if end_time <= start_time:
            start_time, end_time = 0.0, 0.0
        watermark["start_time"] = start_time
        watermark["end_time"] = end_time
    elif watermark["timing_mode"] == "interval":
        duration = _bounded_float(watermark.get("interval_duration"), 5, 0.1, 86_400)
        period = _bounded_float(watermark.get("interval_period"), 30, 0.1, 86_400)
        watermark["interval_duration"] = min(duration, period)
        watermark["interval_period"] = period
    margins = watermark.get("margins")
    if not isinstance(margins, dict):
        margins = {}
    clean_margins = {}
    for side in ("top", "bottom", "left", "right"):
        try:
            clean_margins[side] = max(0, min(500, int(margins.get(side, 10))))
        except (TypeError, ValueError):
            clean_margins[side] = 10
    watermark["margins"] = clean_margins

    settings["watermark"] = watermark
    return settings


def apply_quality_preset(settings: Any, profile: str) -> Dict[str, Any]:
    """Apply only encoding knobs; retain trim, branding, and delivery choices."""
    if profile not in QUALITY_PRESETS:
        raise ValueError(f"Unknown quality profile: {profile}")
    normalized = normalize_settings(settings)
    preset = QUALITY_PRESETS[profile]
    normalized["video"].update(preset["video"])
    normalized["audio"].update(preset["audio"])
    normalized["profile"] = profile
    return normalized
