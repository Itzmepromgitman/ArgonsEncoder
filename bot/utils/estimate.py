# Developed by ARGON telegram: @REACTIVEARGON
"""Deterministic output-size and encode-time estimates.

Pure helpers — no I/O and no client — used to surface a size/time estimate on a
job's early card *before* the encode finishes.  Every public function is total:
it never raises and always returns a well-formed ``(low, high)`` range with
``low <= high``, or ``(0, 0)`` when there is nothing to estimate.

Size model
----------
The size follows a bits-per-pixel (bpp) model::

    bpp          = base_bpp(crf) * codec_bpp_factor
    base_bpp(crf) = 0.12 * 0.87 ** (crf - 23)      # x264 reference
    size_bits    = bpp * pixel_factor * PIXELS_1080P * fps * duration_s
                   + audio_kbps * 1000 * duration_s
    bytes        = size_bits / 8

Constants (bounded, chosen from the public CRF/bitrate literature):

* ``base_bpp``: ``0.12`` at CRF 23, shrinking ~13 % per CRF point
  (``x264`` rule of thumb: +6 CRF ≈ −50 % size).
* Codec factors, relative to x264: ``libx264`` 1.0, ``libx265`` 0.55,
  ``mpeg4`` 1.6, ``libvpx-vp9`` / ``libaom-av1`` 0.44 (= ``0.8 * 0.55``).
* ``PIXELS_1080P`` = 1920 × 1080 reference frame; ``pixel_factor`` is the
  frame's pixel count relative to that: 1080p 1.0, 720p 0.44, 480p 0.2,
  360p 0.11.
* fps is assumed to be 30 when the probe value is unavailable.

The point estimate is widened to a range: ``low ≈ 0.7×`` and ``high ≈ 1.4×``.
When ``source_bytes > 0`` both bounds are clamped into
``[0.04×source, 3.0×source]`` and ``low`` is floored at 40 KB, so a bad
estimate can never suggest a wildly different size than the input.

Time model
----------
Encoding wall time scales with the source duration and is divided by how much
faster the resolved preset runs and how much smaller the frame is::

    seconds = duration_s * codec_factor / (preset_factor * resolution_factor)

``preset_factor`` is relative to ``medium = 1.0`` (``ultrafast 6.0`` …
``veryslow 0.25``); ``codec_factor`` is relative to ``libx264 = 1.0``
(``libx265 2.5``, ``mpeg4 0.8``, ``libvpx-vp9 6.0``, ``libaom-av1 12.0``);
``resolution_factor`` is 1080p 1.0, 720p 0.5, 480p 0.24, 360p 0.14.  The
point estimate is widened to ``low ≈ 0.5×`` / ``high ≈ 2.0×`` and clamped to
``[5 s, 6 h]``.
"""
from __future__ import annotations

import math

from bot.utils.format import humanbytes

_PIXELS_1080P = 1920 * 1080
_ASSUMED_FPS = 30.0

_SIZE_LOW = 0.7
_SIZE_HIGH = 1.4
_MIN_SIZE_BYTES = 40 * 1024
_SIZE_FLOOR_RATIO = 0.04
_SIZE_CEIL_RATIO = 3.0

_TIME_LOW = 0.5
_TIME_HIGH = 2.0
_TIME_MIN_SECONDS = 5
_TIME_MAX_SECONDS = 21600

_CODEC_BPP = {
    "libx264": 1.0,
    "h264": 1.0,
    "x264": 1.0,
    "libx265": 0.55,
    "h265": 0.55,
    "hevc": 0.55,
    "x265": 0.55,
    "mpeg4": 1.6,
    "libvpx-vp9": 0.44,
    "vp9": 0.44,
    "libaom-av1": 0.44,
    "av1": 0.44,
}

_CODEC_TIME = {
    "libx264": 1.0,
    "h264": 1.0,
    "x264": 1.0,
    "libx265": 2.5,
    "h265": 2.5,
    "hevc": 2.5,
    "x265": 2.5,
    "mpeg4": 0.8,
    "libvpx-vp9": 6.0,
    "vp9": 6.0,
    "libaom-av1": 12.0,
    "av1": 12.0,
}

_PRESET_FACTOR = {
    "ultrafast": 6.0,
    "superfast": 4.5,
    "veryfast": 3.5,
    "faster": 2.6,
    "fast": 2.0,
    "medium": 1.0,
    "slow": 0.6,
    "slower": 0.4,
    "veryslow": 0.25,
}

_RESOLUTION_PIXELS = {
    "1080p": 1.0,
    "720p": 0.44,
    "480p": 0.2,
    "360p": 0.11,
}

_RESOLUTION_TIME = {
    "1080p": 1.0,
    "720p": 0.5,
    "480p": 0.24,
    "360p": 0.14,
}

_DEFAULT_CRF = 23
_DEFAULT_AUDIO_KBPS = 128.0


def _safe_duration(value):
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(duration) or duration <= 0:
        return None
    return duration


def _safe_audio_kbps(value):
    try:
        kbps = float(value)
    except (TypeError, ValueError):
        return _DEFAULT_AUDIO_KBPS
    if not math.isfinite(kbps) or kbps <= 0:
        return 0.0
    return min(kbps, 1024.0)


def _safe_source_bytes(value):
    try:
        total = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(total) or total <= 0:
        return 0.0
    return total


def _coerce_crf(value):
    try:
        crf = float(str(value).strip())
    except (TypeError, ValueError):
        return _DEFAULT_CRF
    if not math.isfinite(crf):
        return _DEFAULT_CRF
    crf = round(crf)
    if crf < 0 or crf > 51:
        return _DEFAULT_CRF
    return crf


def _base_bpp(crf):
    return 0.12 * (0.87 ** (crf - 23))


def _codec_bpp(codec):
    return _CODEC_BPP.get(str(codec).strip().lower(), _CODEC_BPP["libx264"])


def _codec_time(codec):
    return _CODEC_TIME.get(str(codec).strip().lower(), _CODEC_TIME["libx264"])


def _preset_factor(preset):
    return _PRESET_FACTOR.get(str(preset).strip().lower(), _PRESET_FACTOR["medium"])


def _resolution_pixels(resolution):
    return _RESOLUTION_PIXELS.get(
        str(resolution).strip().lower(), _RESOLUTION_PIXELS["1080p"]
    )


def _resolution_time(resolution):
    return _RESOLUTION_TIME.get(
        str(resolution).strip().lower(), _RESOLUTION_TIME["1080p"]
    )


def estimate_output_bytes(
    duration_s, resolution, codec, crf, *, audio_kbps=128, source_bytes=0
) -> tuple[int, int]:
    """Estimate the encoded size as a ``(low, high)`` byte range.

    Returns ``(0, 0)`` for a missing/zero duration and never raises.
    """
    duration = _safe_duration(duration_s)
    if duration is None:
        return (0, 0)

    bpp = _base_bpp(_coerce_crf(crf)) * _codec_bpp(codec)
    pixel_factor = _resolution_pixels(resolution)
    audio = _safe_audio_kbps(audio_kbps)

    video_bits = bpp * pixel_factor * _PIXELS_1080P * _ASSUMED_FPS * duration
    audio_bits = audio * 1000.0 * duration
    point_bytes = (video_bits + audio_bits) / 8.0
    if not math.isfinite(point_bytes) or point_bytes <= 0:
        return (0, 0)

    low = round(point_bytes * _SIZE_LOW)
    high = round(point_bytes * _SIZE_HIGH)
    if low > high:
        low, high = high, low

    source = _safe_source_bytes(source_bytes)
    if source > 0:
        floor = round(_SIZE_FLOOR_RATIO * source)
        ceil = max(round(_SIZE_CEIL_RATIO * source), floor)
        low = min(max(low, floor), ceil)
        high = min(max(high, floor), ceil)

    low = max(low, _MIN_SIZE_BYTES)
    high = max(high, low)
    return (low, high)


def estimate_encode_seconds(duration_s, resolution, codec, preset) -> tuple[int, int]:
    """Estimate the encode wall time as a ``(low, high)`` second range.

    Returns ``(0, 0)`` for a missing/zero duration and never raises.
    """
    duration = _safe_duration(duration_s)
    if duration is None:
        return (0, 0)

    divisor = _preset_factor(preset) * _resolution_time(resolution)
    if divisor <= 0:
        divisor = _PRESET_FACTOR["medium"]
    point_seconds = duration * _codec_time(codec) / divisor
    if not math.isfinite(point_seconds) or point_seconds <= 0:
        return (0, 0)

    low = min(max(round(point_seconds * _TIME_LOW), _TIME_MIN_SECONDS),
              _TIME_MAX_SECONDS)
    high = min(max(round(point_seconds * _TIME_HIGH), _TIME_MIN_SECONDS),
               _TIME_MAX_SECONDS)
    high = max(high, low)
    return (low, high)


def _pair(value):
    try:
        low, high = value
        low = int(low)
        high = int(high)
    except (TypeError, ValueError):
        return (0, 0)
    low = max(low, 0)
    high = max(high, 0)
    if low > high:
        low, high = high, low
    return (low, high)


def _format_seconds_range(low, high):
    low = max(1, round(low))
    high = max(low, round(high))
    if high < 90:
        return f"{low}s\u2013{high}s"
    if high < 5400:
        low_min = max(1, round(low / 60))
        high_min = max(low_min, round(high / 60))
        return f"{low_min}\u2013{high_min} min"
    low_hr = max(1, round(low / 3600))
    high_hr = max(low_hr, round(high / 3600))
    return f"{low_hr}\u2013{high_hr} h"


def format_estimate(size_range, secs_range) -> str:
    """Render an estimate line, or ``""`` when there is nothing to show."""
    size_low, size_high = _pair(size_range)
    secs_low, secs_high = _pair(secs_range)

    parts = []
    if size_high > 0:
        low = size_low if size_low > 0 else size_high
        parts.append(f"\U0001F3AF \u2248 {humanbytes(low)}\u2013{humanbytes(size_high)}")
    if secs_high > 0:
        low = secs_low if secs_low > 0 else secs_high
        parts.append(f"\u23F1 \u2248 {_format_seconds_range(low, secs_high)}")
    return " \u00b7 ".join(parts)
