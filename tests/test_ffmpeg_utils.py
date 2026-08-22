from bot.func.ffmpeg_utils import (
    escape_drawtext,
    generate_ffmpeg_cmd,
    sanitize_custom_name,
    validate_ffmpeg_command,
)


def _settings(**video_overrides):
    video = {
        "crf": "23",
        "preset": "medium",
        "resolution": ["1080p"],
        "codec": "libx264",
    }
    video.update(video_overrides)
    return {
        "video": video,
        "audio": {"bitrate": "128k", "codec": "aac", "track": "all"},
        "metadata": {"global": {}, "video": {}, "audio": {}, "subtitle": {}},
        "custom_ffmpeg": {},
    }


def test_single_input_occurrence():
    cmds = generate_ffmpeg_cmd(_settings(), "/in.mkv", "/out")
    assert len(cmds) == 1
    cmd = cmds[0]["cmd"]
    # The input must appear exactly once: the double-input bug regression test.
    assert cmd.split().count("/in.mkv") == 1
    assert "-i" in cmd.split()
    assert cmds[0]["output_file"] == "/out.mkv"


def test_multi_resolution_suffixes():
    s = _settings(resolution=["1080p", "720p"])
    cmds = generate_ffmpeg_cmd(s, "/in.mkv", "/out")
    assert [c["suffix"] for c in cmds] == ["1080p", "720p"]
    assert cmds[0]["output_file"] == "/out_1080p.mkv"
    assert cmds[1]["output_file"] == "/out_720p.mkv"


def test_x264_flags_present():
    cmd = generate_ffmpeg_cmd(_settings(), "/a", "/b")[0]["cmd"]
    assert "-c:v libx264" in cmd
    assert "-crf 23" in cmd
    assert "-preset medium" in cmd
    assert "-c:a aac" in cmd and "-b:a 128k" in cmd
    assert "-c:s copy" in cmd


def test_mpeg4_gets_qscale_not_crf():
    cmd = generate_ffmpeg_cmd(_settings(codec="mpeg4"), "/a", "/b")[0]["cmd"]
    assert "-crf" not in cmd
    assert "-preset" not in cmd
    assert "-qscale:v" in cmd


def test_no_upscale_cap():
    cmd = generate_ffmpeg_cmd(_settings(resolution=["360p"]), "/a", "/b")[0]["cmd"]
    assert "min(ih,360)" in cmd


def test_audio_track_none_strips_audio():
    cmd = generate_ffmpeg_cmd(
        _settings(), "/a", "/b"
    )  # baseline sanity
    s = _settings()
    s["audio"]["track"] = "none"
    cmd = generate_ffmpeg_cmd(s, "/a", "/b")[0]["cmd"]
    assert "-map 0:a?" not in cmd
    assert "-c:a" not in cmd
    assert "-an" not in cmd  # we omit mapping entirely


def test_audio_copy_has_no_bitrate():
    s = _settings()
    s["audio"]["codec"] = "copy"
    cmd = generate_ffmpeg_cmd(s, "/a", "/b")[0]["cmd"]
    assert "-c:a copy" in cmd
    assert "-b:a" not in cmd


def test_subtitle_drop_mode():
    s = _settings(subtitle_mode="drop")
    cmd = generate_ffmpeg_cmd(s, "/a", "/b")[0]["cmd"]
    assert "-sn" in cmd
    assert "-c:s" not in cmd


def test_sample_seconds_flag():
    cmd = generate_ffmpeg_cmd(_settings(sample_seconds=45), "/a", "/b")[0]["cmd"]
    assert "-t 45" in cmd


def test_trim_window_math():
    cmd = generate_ffmpeg_cmd(
        _settings(trim_start=10, trim_end=60), "/a", "/b"
    )[0]["cmd"]
    assert "-ss 10" in cmd
    assert "-t 50" in cmd  # end-start, not -to


def test_remux_stream_copy_skips_filters():
    s = _settings(remux=True)
    s["watermark"] = {"enabled": True, "type": "text", "text": "wm"}
    cmd = generate_ffmpeg_cmd(s, "/a", "/b")[0]["cmd"]
    assert "-c copy" in cmd
    assert "drawtext" not in cmd
    assert "-crf" not in cmd


def test_thumbnail_second_input_and_map():
    cmds = generate_ffmpeg_cmd(_settings(), "/a.mkv", "/b", thumbnail_path="/t.jpg")
    cmd = cmds[0]["cmd"]
    assert "-i /t.jpg" in cmd
    assert "-map 1" in cmd
    assert "attached_pic" in cmd


def test_escape_drawtext():
    assert escape_drawtext("a:b") == "a\\:b"
    assert escape_drawtext("100%") == "100\\%"
    assert escape_drawtext("a,b") == "a\\,b"
    assert escape_drawtext("back\\slash") == "back\\\\slash"
    assert "'" not in escape_drawtext("it's")


def test_sanitize_custom_name():
    assert sanitize_custom_name("my_preset-1") == "my_preset-1"
    assert sanitize_custom_name("../evil") is None
    assert sanitize_custom_name("$startsWithDollar") is None
    assert sanitize_custom_name("") is None
    assert sanitize_custom_name("x" * 33) is None
    assert sanitize_custom_name("x" * 32) is not None


def test_validate_ffmpeg_command():
    assert validate_ffmpeg_command("-c:v libx264 -crf 23") is True
    assert validate_ffmpeg_command("-i something") is False
    assert validate_ffmpeg_command("-y") is False
    assert validate_ffmpeg_command("   ") is False
