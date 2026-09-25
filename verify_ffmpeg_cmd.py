# Developed by ARGON telegram: @REACTIVEARGON
"""Smoke-test the ffmpeg command generator. Run: python verify_ffmpeg_cmd.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot.func.ffmpeg_utils import generate_ffmpeg_cmd  # noqa: E402


def test_generate_ffmpeg_cmd():
    settings = {
        "video": {"crf": "23", "preset": "medium", "resolution": ["1080p"], "codec": "libx264"},
        "audio": {"bitrate": "128k", "codec": "aac", "track": "all"},
        "metadata": {"global": {}, "video": {}, "audio": {}, "subtitle": {}},
        "custom_ffmpeg": {},
    }
    commands = generate_ffmpeg_cmd(settings, "/tmp/in.mkv", "/tmp/out")
    assert len(commands) == 1
    cmd = commands[0]["cmd"]
    assert "-i /tmp/in.mkv" in cmd
    assert cmd.count("/tmp/in.mkv") == 1, "input must appear exactly once"
    assert "-c:v libx264" in cmd
    assert "-crf 23" in cmd
    assert commands[0]["output_file"] == "/tmp/out.mkv"
    print("PASS: single-res x264 command")
    return commands


def test_mpeg4_no_crf():
    settings = {
        "video": {"crf": "23", "preset": "medium", "resolution": ["720p"], "codec": "mpeg4"},
        "audio": {"bitrate": "128k", "codec": "copy", "track": "none"},
        "metadata": {"global": {}, "video": {}, "audio": {}, "subtitle": {}},
        "custom_ffmpeg": {},
    }
    commands = generate_ffmpeg_cmd(settings, "/tmp/in.mkv", "/tmp/o")
    cmd = commands[0]["cmd"]
    assert "-crf" not in cmd, "mpeg4 must not receive -crf"
    assert "-qscale:v" in cmd
    assert "-c:a copy" not in cmd, "no audio mapped when track=none"
    print("PASS: mpeg4 flag mapping + audio strip")


def test_sample_trim_remux():
    base = {
        "video": {
            "crf": "23",
            "preset": "medium",
            "resolution": ["1080p"],
            "codec": "libx264",
            "sample_seconds": 30,
            "trim_start": 10,
            "trim_end": 60,
        },
        "audio": {"bitrate": "128k", "codec": "aac"},
        "metadata": {"global": {}, "video": {}, "audio": {}, "subtitle": {}},
        "custom_ffmpeg": {},
    }
    cmd = generate_ffmpeg_cmd(base, "/a.mkv", "/b")[0]["cmd"]
    assert "-t 30" in cmd and "-ss 10" in cmd

    base["video"]["remux"] = True
    cmd = generate_ffmpeg_cmd(base, "/a.mkv", "/b")[0]["cmd"]
    assert "-c copy" in cmd
    assert "-crf" not in cmd and "drawtext" not in cmd
    print("PASS: sample/trim/remux flags")


if __name__ == "__main__":
    test_generate_ffmpeg_cmd()
    test_mpeg4_no_crf()
    test_sample_trim_remux()
    sys.exit(0)
