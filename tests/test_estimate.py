"""Tier-0 tests for output-size / encode-time estimates.

The estimators are pure and must never raise, even on garbage input.
"""

from bot.utils.estimate import (
    estimate_encode_seconds,
    estimate_output_bytes,
    format_estimate,
)


def _is_range(value):
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(part, int) for part in value)
        and value[0] <= value[1]
    )


def test_output_bytes_happy_path_is_well_formed():
    low, high = estimate_output_bytes(60.0, "1080p", "libx264", 23)
    assert _is_range((low, high))
    assert 0 < low <= high


def test_encode_seconds_happy_path_is_well_formed():
    low, high = estimate_encode_seconds(60.0, "1080p", "libx264", "medium")
    assert _is_range((low, high))
    assert 0 < low <= high


def test_estimators_are_deterministic():
    assert estimate_output_bytes(90.0, "720p", "libx265", 28) == estimate_output_bytes(
        90.0, "720p", "libx265", 28
    )
    assert estimate_encode_seconds(90.0, "720p", "libx265", "slow") == (
        estimate_encode_seconds(90.0, "720p", "libx265", "slow")
    )


def test_output_bytes_zero_or_negative_duration():
    assert estimate_output_bytes(0, "1080p", "libx264", 23) == (0, 0)
    assert estimate_output_bytes(-30, "1080p", "libx264", 23) == (0, 0)


def test_encode_seconds_zero_or_negative_duration():
    assert estimate_encode_seconds(0, "1080p", "libx264", "medium") == (0, 0)
    assert estimate_encode_seconds(-30, "1080p", "libx264", "medium") == (0, 0)


def test_output_bytes_unknown_codec_does_not_raise():
    result = estimate_output_bytes(60.0, "1080p", "not-a-codec", 23)
    assert _is_range(result)
    assert result[0] >= 0


def test_output_bytes_unknown_resolution_does_not_raise():
    result = estimate_output_bytes(60.0, "9999p", "libx264", 23)
    assert _is_range(result)
    assert result[0] >= 0


def test_encode_seconds_unknown_preset_does_not_raise():
    result = estimate_encode_seconds(60.0, "1080p", "libx264", "insane")
    assert _is_range(result)
    assert result[0] >= 0


def test_encode_seconds_unknown_codec_and_resolution_does_not_raise():
    result = estimate_encode_seconds(60.0, "9999p", "not-a-codec", "medium")
    assert _is_range(result)
    assert result[0] >= 0


def test_output_bytes_crf_out_of_range_does_not_raise():
    for crf in (-5, 0, 51, 999):
        result = estimate_output_bytes(60.0, "1080p", "libx264", crf)
        assert _is_range(result)
        assert result[0] >= 0


def test_output_bytes_non_numeric_crf_does_not_raise():
    for crf in ("abc", None, [], {}):
        result = estimate_output_bytes(60.0, "1080p", "libx264", crf)
        assert _is_range(result)
        assert result[0] >= 0


def test_output_bytes_size_decreases_as_crf_rises():
    low_crf = estimate_output_bytes(120.0, "1080p", "libx264", 18)
    high_crf = estimate_output_bytes(120.0, "1080p", "libx264", 28)
    assert high_crf[1] <= low_crf[1]
    assert high_crf[0] <= low_crf[0]


def test_encode_seconds_time_increases_as_preset_slows():
    fast = estimate_encode_seconds(120.0, "1080p", "libx264", "ultrafast")
    slow = estimate_encode_seconds(120.0, "1080p", "libx264", "veryslow")
    assert slow[1] >= fast[1]
    assert slow[0] >= fast[0]


def test_output_bytes_clamped_by_source_bytes():
    source = 10 * 1024 * 1024
    low, high = estimate_output_bytes(
        3600.0, "1080p", "libx264", 18, source_bytes=source
    )
    assert low <= high
    assert low >= 0
    assert high <= 3.0 * source


def test_format_estimate_empty():
    assert format_estimate((0, 0), (0, 0)) == ""


def test_format_estimate_normal_is_single_line_with_approx():
    text = format_estimate((1024, 2048), (3, 9))
    assert text
    assert "\n" not in text
    assert "≈" in text
