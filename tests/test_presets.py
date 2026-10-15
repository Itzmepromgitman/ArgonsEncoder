"""Tier-0 tests for named encoding presets (built-in + user-saved)."""

from bot.utils.presets import (
    BUILTIN_PRESETS,
    MAX_USER_PRESETS,
    extract_overlay,
    merged_presets,
    overlay_summary,
    sanitize_overlay,
    sanitize_user_presets,
    user_preset_key,
)
from bot.utils.settings import apply_settings_preset, normalize_settings


def test_builtin_presets_are_well_formed():
    assert BUILTIN_PRESETS
    for key, preset in BUILTIN_PRESETS.items():
        assert key == key.lower()
        assert preset["label"] and preset["description"]
        # Every built-in overlay must survive sanitizing unchanged.
        assert sanitize_overlay(preset["value"]) == preset["value"]


def test_builtin_overlays_survive_normalization():
    """A built-in preset must not be silently rewritten by the sanitizer."""
    base = normalize_settings({})
    for preset in BUILTIN_PRESETS.values():
        applied = apply_settings_preset(base, preset["value"])
        overlay = sanitize_overlay(preset["value"])
        assert applied["video"]["resolution"] == overlay["video"]["resolution"]
        assert applied["video"]["codec"] == overlay["video"]["codec"]
        assert applied["video"]["crf"] == overlay["video"]["crf"]
        assert applied["audio"]["codec"] == overlay["audio"]["codec"]


def test_user_preset_key():
    assert user_preset_key("My 1080p") == "u_my_1080p"
    assert user_preset_key("Reels 720p") == "u_reels_720p"
    assert user_preset_key("  Spaced  Name ") == "u_spaced_name"
    assert user_preset_key("Reels!!") == ""
    assert user_preset_key("") == ""
    assert user_preset_key("-lead") == ""
    assert user_preset_key("a" * 24).startswith("u_")
    assert user_preset_key("a" * 25) == ""


def test_sanitize_overlay_shapes_and_drops():
    overlay = sanitize_overlay(
        {
            "video": {
                "resolution": ["1080p", "9999p", "1080p"],
                "crf": 99,
                "codec": "libx264",
                "bogus": "drop-me",
            },
            "audio": {"bitrate": "128k"},
            "watermark": {"enabled": True},
            "thumbnails": ["x"],
        }
    )
    assert overlay["video"]["resolution"] == ["1080p"]
    assert overlay["video"]["crf"] == "51"
    assert "bogus" not in overlay["video"]
    assert overlay["audio"] == {"bitrate": "128k"}
    assert "watermark" not in overlay
    assert "thumbnails" not in overlay


def test_sanitize_overlay_rejects_non_dict():
    assert sanitize_overlay(None) == {}
    assert sanitize_overlay("nope") == {}


def test_sanitize_user_presets_limits_and_validates():
    raw = {}
    for index in range(MAX_USER_PRESETS + 5):
        raw[f"Preset {index}"] = {"video": {"crf": 20}}
    raw["bad name !!!"] = {"video": {"crf": 20}}
    raw["web_1080p"] = {"video": {"crf": 20}}
    raw[123] = {"video": {"crf": 20}}
    raw["Empty"] = {}
    clean = sanitize_user_presets(raw)
    assert len(clean) == MAX_USER_PRESETS
    assert all(key.startswith("u_") for key in clean)
    assert "web_1080p" not in clean
    assert all(entry["value"] for entry in clean.values())


def test_sanitize_user_presets_tolerates_garbage():
    assert sanitize_user_presets(None) == {}
    assert sanitize_user_presets("nope") == {}
    assert sanitize_user_presets({"X": "not-a-dict"}) == {}


def test_extract_overlay_from_settings():
    settings = normalize_settings(
        {
            "video": {"resolution": ["720p"], "codec": "libx265", "crf": "28"},
            "audio": {"codec": "ac3", "bitrate": "192k"},
            "output_as_video": True,
        }
    )
    overlay = extract_overlay(settings)
    assert overlay["video"]["resolution"] == ["720p"]
    assert overlay["video"]["codec"] == "libx265"
    assert overlay["audio"]["codec"] == "ac3"
    assert overlay["output_as_video"] is True


def test_apply_settings_preset_keeps_branding_and_trim():
    settings = normalize_settings(
        {
            "video": {"trim_start": 5, "trim_end": 15},
            "watermark": {"enabled": True, "text": "Mine"},
            "metadata": {"global": {"title": "Keep me"}},
            "rename": {"pattern": "original_{res}"},
        }
    )
    applied = apply_settings_preset(settings, BUILTIN_PRESETS["compact_480p"]["value"])
    assert applied["watermark"]["enabled"] is True
    assert applied["watermark"]["text"] == "Mine"
    assert applied["metadata"]["global"]["title"] == "Keep me"
    assert applied["rename"]["pattern"] == "original_{res}"
    assert applied["video"]["trim_start"] == 5
    assert applied["video"]["trim_end"] == 15
    assert applied["video"]["resolution"] == ["480p"]
    assert applied["video"]["codec"] == "libx265"


def test_apply_settings_preset_is_normalized():
    """An out-of-range overlay is clamped, not written through."""
    applied = apply_settings_preset(
        normalize_settings({}),
        {"video": {"crf": 999, "codec": "not-a-codec", "resolution": ["888p"]}},
    )
    assert applied["video"]["crf"] == "51"
    assert applied["video"]["codec"] == "libx264"
    assert applied["video"]["resolution"] == ["1080p"]


def test_overlay_summary():
    overlay = sanitize_overlay(
        {
            "video": {"resolution": ["720p"], "codec": "libx264", "crf": "24"},
            "audio": {"codec": "aac", "bitrate": "128k"},
        }
    )
    summary = overlay_summary(overlay)
    assert "720p" in summary and "CRF24" in summary and "aac 128k" in summary
    assert overlay_summary({}) == "—"


def test_merged_presets_order_and_flags():
    user = sanitize_user_presets({"My Clip": {"video": {"crf": "22"}}})
    merged = merged_presets(user)
    keys = list(merged)
    assert keys[: len(BUILTIN_PRESETS)] == list(BUILTIN_PRESETS)
    assert keys[-1] == "u_my_clip"
    assert merged["web_1080p"]["builtin"] is True
    assert merged["u_my_clip"]["builtin"] is False
    assert merged["u_my_clip"]["label"] == "My Clip"


def test_normalize_settings_keeps_presets():
    settings = normalize_settings({"presets": {"My Clip": {"video": {"crf": "22"}}}})
    assert "u_my_clip" in settings["presets"]
    # Garbage is dropped, not raised on.
    assert normalize_settings({"presets": "garbage"})["presets"] == {}
