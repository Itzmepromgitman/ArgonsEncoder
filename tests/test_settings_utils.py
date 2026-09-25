from bot.utils.settings import apply_quality_preset, normalize_settings


def test_normalize_repairs_legacy_and_out_of_range_values():
    settings = normalize_settings(
        {
            "video": {
                "codec": "invalid",
                "crf": 99,
                "resolution": "720p",
                "sample_seconds": 4,
                "trim_start": 50,
                "trim_end": 20,
            },
            "audio": {"bitrate": "9999k", "track": "0"},
            "active_custom_ffmpeg": "missing",
        }
    )

    assert settings["video"]["codec"] == "libx264"
    assert settings["video"]["crf"] == "51"
    assert settings["video"]["resolution"] == ["720p"]
    assert settings["video"]["sample_seconds"] == 0
    assert settings["video"]["trim_start"] == 0
    assert settings["video"]["trim_end"] == 0
    assert settings["audio"]["bitrate"] == "128k"
    assert settings["audio"]["track"] == "1"
    assert settings["active_custom_ffmpeg"] == ""


def test_rename_pattern_rejects_unbounded_format_specs():
    settings = normalize_settings({"rename": {"pattern": "{original:*>999999999}"}})
    assert settings["rename"]["pattern"] == ""


def test_normalize_is_defensive_and_preserves_branding():
    raw = {
        "watermark": {"enabled": True, "text": "Mine", "opacity": 9},
        "thumbnail": b"legacy",
    }
    first = normalize_settings(raw)
    first["video"]["crf"] = "10"
    first["watermark"]["text"] = "changed"

    second = normalize_settings(raw)
    assert second["video"]["crf"] == "23"
    assert second["watermark"]["text"] == "Mine"
    assert second["watermark"]["opacity"] == 1.0
    assert second["thumbnail"] == b"legacy"


def test_quality_preset_retains_non_quality_settings():
    settings = normalize_settings(
        {
            "video": {"resolution": ["720p", "360p"], "trim_start": 5, "trim_end": 20},
            "rename": {"pattern": "{original}-{res}"},
            "output_as_video": True,
        }
    )
    compact = apply_quality_preset(settings, "compact")

    assert compact["profile"] == "compact"
    assert compact["video"]["codec"] == "libx265"
    assert compact["video"]["crf"] == "28"
    assert compact["video"]["resolution"] == ["720p", "360p"]
    assert compact["video"]["trim_start"] == 5
    assert compact["video"]["trim_end"] == 20
    assert compact["rename"]["pattern"] == "{original}-{res}"
    assert compact["output_as_video"] is True
