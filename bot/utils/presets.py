"""Named encoding presets: built-in starting points plus user-saved ones.

A preset is a bounded *overlay* over the encoding knobs only — resolution,
video codec, CRF, encoder preset, remux/subtitle mode, audio codec/bitrate/
track, and delivery format. Applying a preset therefore never touches trim,
metadata, rename patterns, watermarks or thumbnails, so a one-tap preset can
never silently delete a user's branding.

Keys are stable, lower-case slugs so a preset can travel inside a Telegram deep
link: ``https://t.me/<bot>?start=preset_web_1080p``.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Dict, List

MAX_USER_PRESETS = 12
MAX_LABEL_LEN = 40

# A user-chosen preset name: starts alphanumeric, then a bounded mix of
# letters, digits, spaces, underscores and dashes.
_PRESET_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,23}")

_RESOLUTIONS = ("1080p", "720p", "480p", "360p")
_SUBTITLE_MODES = ("copy", "drop")

# Stable, lower-case keys; labels/descriptions mirror the rest of the UI voice.
BUILTIN_PRESETS: "OrderedDict[str, Dict[str, Any]]" = OrderedDict(
    (
        (
            "web_1080p",
            {
                "label": "🌐 Web 1080p",
                "description": "1080p · H.264 CRF23 · 128k",
                "value": {
                    "video": {
                        "resolution": ["1080p"],
                        "codec": "libx264",
                        "crf": "23",
                        "preset": "medium",
                        "remux": False,
                        "subtitle_mode": "copy",
                    },
                    "audio": {"codec": "aac", "bitrate": "128k", "track": "all"},
                    "output_as_video": True,
                },
            },
        ),
        (
            "social_720p",
            {
                "label": "📱 Social 720p",
                "description": "720p · H.264 veryfast CRF24 · 128k",
                "value": {
                    "video": {
                        "resolution": ["720p"],
                        "codec": "libx264",
                        "crf": "24",
                        "preset": "veryfast",
                        "remux": False,
                        "subtitle_mode": "copy",
                    },
                    "audio": {"codec": "aac", "bitrate": "128k", "track": "all"},
                    "output_as_video": True,
                },
            },
        ),
        (
            "compact_480p",
            {
                "label": "📦 Compact 480p",
                "description": "480p · HEVC slow CRF28 · 96k",
                "value": {
                    "video": {
                        "resolution": ["480p"],
                        "codec": "libx265",
                        "crf": "28",
                        "preset": "slow",
                        "remux": False,
                        "subtitle_mode": "copy",
                    },
                    "audio": {"codec": "aac", "bitrate": "96k", "track": "all"},
                    "output_as_video": False,
                },
            },
        ),
        (
            "tiny_360p",
            {
                "label": "🪶 Tiny 360p",
                "description": "360p · H.264 veryfast CRF30 · 96k",
                "value": {
                    "video": {
                        "resolution": ["360p"],
                        "codec": "libx264",
                        "crf": "30",
                        "preset": "veryfast",
                        "remux": False,
                        "subtitle_mode": "copy",
                    },
                    "audio": {"codec": "aac", "bitrate": "96k", "track": "all"},
                    "output_as_video": False,
                },
            },
        ),
    )
)


def _coerce_int(value: Any, low: int, high: int, default: str) -> str:
    try:
        return str(max(low, min(high, int(value))))
    except (TypeError, ValueError):
        return default


def user_preset_key(name: str) -> str:
    """Return a stable, deep-link-safe key for a user preset name, or ``""``."""
    name = str(name).strip()
    if not _PRESET_NAME_RE.fullmatch(name):
        return ""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug:
        return ""
    return ("u_" + slug)[:40]


def sanitize_overlay(raw: Any) -> Dict[str, Any]:
    """Shape an untrusted overlay to the allowed encoding fields.

    Values are shaped but not range-checked here; the settings normalizer runs
    afterwards and clamps codecs, CRF, bitrate and friends to their valid sets.
    """
    if not isinstance(raw, dict):
        return {}

    overlay: Dict[str, Any] = {}
    video_in = raw.get("video")
    if isinstance(video_in, dict):
        video: Dict[str, Any] = {}
        if "resolution" in video_in:
            resolutions = video_in["resolution"]
            if isinstance(resolutions, str):
                resolutions = [resolutions]
            if isinstance(resolutions, list):
                clean_res = [
                    res for res in resolutions if res in _RESOLUTIONS
                ]
                clean_res = list(dict.fromkeys(clean_res))
                if clean_res:
                    video["resolution"] = clean_res
        if "codec" in video_in:
            video["codec"] = str(video_in["codec"])[:16]
        if "crf" in video_in:
            video["crf"] = _coerce_int(video_in["crf"], 0, 51, "23")
        if "preset" in video_in:
            video["preset"] = str(video_in["preset"])[:16]
        if "remux" in video_in:
            video["remux"] = bool(video_in["remux"])
        if "subtitle_mode" in video_in:
            video["subtitle_mode"] = (
                "drop" if video_in["subtitle_mode"] in ("drop", True) else "copy"
            )
        if video:
            overlay["video"] = video

    audio_in = raw.get("audio")
    if isinstance(audio_in, dict):
        audio: Dict[str, Any] = {}
        if "codec" in audio_in:
            audio["codec"] = str(audio_in["codec"])[:16]
        if "bitrate" in audio_in:
            audio["bitrate"] = str(audio_in["bitrate"])[:8]
        if "track" in audio_in:
            audio["track"] = str(audio_in["track"])[:8]
        if audio:
            overlay["audio"] = audio

    if "output_as_video" in raw:
        overlay["output_as_video"] = bool(raw["output_as_video"])

    return overlay


def extract_overlay(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Pull just the encoding knobs out of a normalized settings document."""
    video = settings.get("video", {}) if isinstance(settings, dict) else {}
    audio = settings.get("audio", {}) if isinstance(settings, dict) else {}
    return sanitize_overlay(
        {
            "video": {
                "resolution": list(video.get("resolution", ["1080p"])),
                "codec": video.get("codec", "libx264"),
                "crf": video.get("crf", "23"),
                "preset": video.get("preset", "medium"),
                "remux": video.get("remux", False),
                "subtitle_mode": video.get("subtitle_mode", "copy"),
            },
            "audio": {
                "codec": audio.get("codec", "aac"),
                "bitrate": audio.get("bitrate", "128k"),
                "track": audio.get("track", "all"),
            },
            "output_as_video": settings.get("output_as_video", False)
            if isinstance(settings, dict)
            else False,
        }
    )


def sanitize_user_presets(raw: Any) -> Dict[str, Any]:
    """Validate the persisted ``presets`` map: ``key -> {label, value}``."""
    if not isinstance(raw, dict):
        return {}
    clean: Dict[str, Any] = {}
    for name, entry in raw.items():
        if len(clean) >= MAX_USER_PRESETS:
            break
        if not isinstance(name, str):
            continue
        key = user_preset_key(name)
        if not key or key in BUILTIN_PRESETS or key in clean:
            continue
        if isinstance(entry, dict) and "value" in entry:
            label = str(entry.get("label", name))[:MAX_LABEL_LEN]
            value = sanitize_overlay(entry.get("value"))
        else:
            label = str(name)[:MAX_LABEL_LEN]
            value = sanitize_overlay(entry)
        if value:
            clean[key] = {"label": label, "value": value}
    return clean


def overlay_summary(overlay: Dict[str, Any]) -> str:
    """One-line, human summary of an overlay for cards and buttons."""
    video = overlay.get("video", {}) if isinstance(overlay, dict) else {}
    audio = overlay.get("audio", {}) if isinstance(overlay, dict) else {}
    parts: List[str] = []
    resolutions = video.get("resolution")
    if resolutions:
        parts.append("/".join(resolutions))
    if video.get("codec"):
        parts.append(str(video["codec"]))
    if video.get("crf"):
        parts.append(f"CRF{video['crf']}")
    if video.get("preset"):
        parts.append(str(video["preset"]))
    if audio.get("codec"):
        audio_bit = f"{audio['codec']} {audio.get('bitrate', '')}".strip()
        parts.append(audio_bit)
    return " · ".join(parts) if parts else "—"


def merged_presets(user_presets: Dict[str, Any]) -> "OrderedDict[str, Dict[str, Any]]":
    """Built-ins first, then user presets, each as ``{label, description, value}``."""
    merged: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for key, preset in BUILTIN_PRESETS.items():
        merged[key] = {
            "label": preset["label"],
            "description": preset["description"],
            "value": preset["value"],
            "builtin": True,
        }
    for key, preset in (user_presets or {}).items():
        if key in BUILTIN_PRESETS:
            continue
        merged[key] = {
            "label": preset.get("label", key),
            "description": overlay_summary(preset.get("value", {})),
            "value": preset.get("value", {}),
            "builtin": False,
        }
    return merged
