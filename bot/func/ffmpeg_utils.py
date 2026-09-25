# Developed by ARGON telegram: @REACTIVEARGON
import os
import re
import shlex
from typing import Dict, List, Optional

from bot.config import (
    FFMPEG_BIN,
    FFMPEG_THREADS,
    FONT_PATH,
    MAX_OUTPUT_SIZE,
    THUMB_DIR,
    WATERMARK_DIR,
)
from bot.logger import LOGGER

log = LOGGER(__name__)

VALID_CODECS = {"libx264", "libx265", "libvpx-vp9", "libaom-av1", "mpeg4"}
VALID_PRESETS = [
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow",
]
VALID_AUDIO_CODECS = {"aac", "ac3", "copy"}


def validate_ffmpeg_command(cmd: str) -> bool:
    """Validate a bounded, encoder-only FFmpeg override.

    Input/output selection, stream mapping, and container destinations are
    managed by the bot.  Values are checked semantically as well as by option
    name so a custom command cannot use an apparently harmless flag to discard
    arbitrary streams or create an unbounded resource request.
    """
    if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > 1000:
        return False

    flags_without_values = {
        "-copyts", "-start_at_zero",
    }
    flags_with_values = {
        "-crf", "-preset", "-b:v",
        "-b:a", "-cpu-used", "-qscale:v", "-profile:v", "-level", "-tag:v",
        "-pix_fmt", "-r", "-g", "-keyint_min", "-sc_threshold", "-movflags",
        "-max_muxing_queue_size", "-threads", "-metadata", "-metadata:s:v",
        "-metadata:s:a", "-metadata:s:s",
    }
    allowed = flags_without_values | flags_with_values
    metadata_keys = {
        "title", "artist", "album", "album_artist", "genre", "track", "disc", "date",
        "year", "comment", "description", "composer", "performer", "publisher",
        "lyrics", "synopsis", "copyright", "language",
        "show", "season_number", "episode", "network", "rotate", "fps",
        "resolution", "bit_rate",
    }
    profiles = {"baseline", "main", "high", "high10", "high422", "high444"}
    pixel_formats = {"yuv420p", "yuv422p", "yuv444p", "yuv420p10le", "yuv422p10le", "yuv444p10le", "nv12", "gray"}

    def bounded_number(value: str, low: float, high: float) -> bool:
        try:
            return low <= float(value) <= high
        except (TypeError, ValueError):
            return False

    def valid_value(flag: str, value: str) -> bool:
        if not value or len(value) > 240 or "\x00" in value:
            return False
        if "://" in value or value.startswith(("/", "~", "\\")):
            return False
        if flag in {"-preset"}:
            return value.lower() in {
                "ultrafast", "superfast", "veryfast", "faster", "fast", "medium",
                "slow", "slower", "veryslow",
            }
        if flag == "-crf":
            return bounded_number(value, 0, 63)
        if flag in {"-qscale:v", "-cpu-used"}:
            return bounded_number(
                value,
                0 if flag == "-cpu-used" else 1,
                8 if flag == "-cpu-used" else 31,
            )
        if flag in {"-b:v", "-b:a"}:
            match = re.fullmatch(r"(\d{1,7})([kKmM]?)", value)
            return bool(
                match
                and (0 if flag == "-b:v" else 1)
                <= int(match.group(1))
                <= 50_000_000
            )
        if flag == "-profile:v":
            return value.lower() in profiles
        if flag == "-level":
            return bool(re.fullmatch(r"\d{1,3}(?:\.\d+)?", value))
        if flag == "-tag:v":
            return bool(re.fullmatch(r"[A-Za-z0-9]{4}", value))
        if flag == "-pix_fmt":
            return value.lower() in pixel_formats
        if flag in {"-r", "-g", "-keyint_min", "-sc_threshold", "-max_muxing_queue_size", "-threads"}:
            if flag == "-threads":
                return bounded_number(value, 1, 8)
            if flag == "-max_muxing_queue_size":
                return bounded_number(value, 1, 8192)
            if flag == "-r":
                return bounded_number(value, 1, 60)
            return bounded_number(value, 0 if flag == "-sc_threshold" else 1, 1_000_000)
        if flag == "-movflags":
            return bool(re.fullmatch(r"[A-Za-z0-9_+-]{1,100}", value))
        if flag.startswith("-metadata"):
            if "=" not in value:
                return False
            key, metadata_value = value.split("=", 1)
            return key.lower() in metadata_keys and len(metadata_value) <= 200 and "\\" not in metadata_value
        return True

    try:
        args = shlex.split(cmd)
    except ValueError:
        return False
    if not args or len(args) > 48:
        return False

    expect_value = False
    current_flag = ""
    for arg in args:
        lowered = arg.lower()
        if expect_value:
            if not valid_value(current_flag, arg):
                return False
            expect_value = False
            current_flag = ""
            continue
        if lowered not in allowed:
            return False
        if lowered in flags_with_values:
            expect_value = True
            current_flag = lowered

    return not expect_value


def sanitize_custom_name(name: str) -> Optional[str]:
    """Whitelist custom command names so they are safe as Mongo keys and callback data."""
    name = (name or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name):
        return name
    return None


def _is_safe_asset_path(path: str, root: str) -> bool:
    try:
        return os.path.commonpath(
            (os.path.realpath(path), os.path.realpath(root))
        ) == os.path.realpath(root)
    except (OSError, ValueError):
        return False


def _write_asset(path: str, data: bytes) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return True
    except Exception as e:
        log.error(f"Failed to write asset {path}: {e}")
        return False


def prepare_watermark_assets(user_id: int, settings: Dict):
    """
    Restores watermark assets to disk if missing.
    Legacy blobs stored inside settings are migrated to disk and stripped from the DB doc.
    """
    wm = settings.get("watermark", {})
    changed = False

    image_path = os.path.join(WATERMARK_DIR, f"{user_id}.png")
    font_path = os.path.join(WATERMARK_DIR, "fonts", f"{user_id}.ttf")

    # Migrate legacy inline blobs out of Mongo
    if isinstance(wm.get("image_data"), bytes):
        if not os.path.exists(image_path):
            _write_asset(image_path, wm["image_data"])
        del wm["image_data"]
        changed = True
    if isinstance(wm.get("font_data"), bytes):
        if not os.path.exists(font_path):
            _write_asset(font_path, wm["font_data"])
        del wm["font_data"]
        changed = True

    if changed:
        settings["watermark"] = wm

    if not os.path.exists(font_path) and not os.path.exists(image_path):
        return


def prepare_thumbnail(user_id: int, settings: Dict) -> Optional[str]:
    """Returns the user's thumbnail path (file already on disk) or None."""
    thumb = settings.get("thumbnail")
    if not thumb:
        return None
    if isinstance(thumb, str):
        if not _is_safe_asset_path(thumb, THUMB_DIR):
            log.warning("Ignoring thumbnail path outside THUMB_DIR")
            return None
        return thumb if os.path.exists(thumb) else None
    if isinstance(thumb, bytes):
        thumb_path = os.path.join(THUMB_DIR, f"{user_id}.jpg")
        if not os.path.exists(thumb_path):
            if not _write_asset(thumb_path, thumb):
                return None
        return thumb_path
    return None


def escape_drawtext(text: str) -> str:
    r"""Escape text for use inside a drawtext filter body. Order matters."""
    text = text.replace("\\", "\\\\")
    text = text.replace("%", "\\%")
    text = text.replace(":", "\\:")
    text = text.replace(",", "\\,")
    text = text.replace("'", "")
    text = text.replace("\n", " ")
    return text


def generate_watermark_filter(settings: Dict, for_preview: bool = False) -> str:
    """Generates the FFmpeg filter string for watermarks."""
    wm_settings = settings.get("watermark", {})
    if not isinstance(wm_settings, dict):
        return ""
    wm_type = wm_settings.get("type", "none")

    def number(key, default, low, high):
        try:
            return max(low, min(high, float(wm_settings.get(key, default))))
        except (TypeError, ValueError):
            return default

    if not wm_settings.get("enabled", False) or wm_type == "none":
        return ""

    position = wm_settings.get("position", "top-right")
    opacity = number("opacity", 0.5, 0.1, 1.0)

    timing_mode = wm_settings.get("timing_mode", "always")
    start_time = number("start_time", 0, 0, 86_400)
    end_time = number("end_time", 0, 0, 86_400)
    interval_duration = number("interval_duration", 5, 0.1, 86_400)
    interval_period = number("interval_period", 30, 0.1, 86_400)

    enable_expr = ""
    if timing_mode == "range":
        enable_expr = f":enable='between(t,{start_time},{end_time})'"
    elif timing_mode == "interval":
        enable_expr = (
            f":enable='lt(mod(t,{interval_period}),{interval_duration})'"
        )

    margins = wm_settings.get("margins", {})
    if not isinstance(margins, dict):
        margins = {}
    def margin(side):
        try:
            return max(0, min(500, int(margins.get(side, 10))))
        except (TypeError, ValueError):
            return 10
    m_top = margin("top")
    m_bottom = margin("bottom")
    m_left = margin("left")
    m_right = margin("right")

    # Overlay expressions: uppercase W/H refer to the main video,
    # lowercase w/h to the overlay itself.
    if position == "top-left":
        x, y = f"{m_left}", f"{m_top}"
    elif position == "top-right":
        x, y = f"W-w-{m_right}", f"{m_top}"
    elif position == "bottom-left":
        x, y = f"{m_left}", f"H-h-{m_bottom}"
    else:  # bottom-right
        x, y = f"W-w-{m_right}", f"H-h-{m_bottom}"

    credit_text = ""
    if for_preview:
        preview_font = (
            FONT_PATH.replace("\\", "/")
            .replace(":", r"\:")
            .replace("'", r"\'")
        )
        credit_text = (
            f",drawtext=fontfile='{preview_font}'"
            ":text='Developed by Argon':fontsize=24:fontcolor=black"
            ":x=W-tw-10:y=H-th-10"
        )

    if wm_type == "text":
        text = str(wm_settings.get("text", "Argons Encoder"))
        try:
            font_size = max(10, min(100, int(wm_settings.get("font_size", 24))))
        except (TypeError, ValueError):
            font_size = 24
        border_opacity = number("border_opacity", 0.5, 0.0, 1.0)

        user_id = settings.get("user_id")
        font_path = FONT_PATH
        if user_id:
            custom_font = os.path.join(WATERMARK_DIR, "fonts", f"{user_id}.ttf")
            if os.path.exists(custom_font):
                font_path = custom_font
        font_path = font_path.replace("\\", "/").replace(":", r"\:")
        safe_text = escape_drawtext(text)

        return (
            f"drawtext=fontfile='{font_path}':text='{safe_text}':fontsize={font_size}"
            f":fontcolor=white@{opacity}:x={x}:y={y}"
            f":box=1:boxcolor=black@{border_opacity}:boxborderw=5{enable_expr}"
            f"{credit_text}"
        )

    elif wm_type == "image":
        image_path = wm_settings.get("image_path", "")
        if (
            not image_path
            or not _is_safe_asset_path(str(image_path), WATERMARK_DIR)
            or not os.path.exists(str(image_path))
        ):
            log.warning(f"Watermark image missing on disk: {image_path}")
            return ""

        scale = number("scale", 0.1, 0.1, 1.0)

        try:
            image_path = os.path.relpath(image_path, os.getcwd())
        except ValueError:
            pass
        image_path = image_path.replace("\\", "/").replace("'", r"\'").replace(":", r"\:")

        return (
            f"movie='{image_path}',scale=iw*{scale}:-1,format=rgba,"
            f"colorchannelmixer=aa={opacity}[wm];"
            f"[0:v][wm]overlay=x='{x}':y='{y}'{enable_expr}"
            f"{credit_text}"
        )

    return ""


def _video_codec_args(codec: str, crf: str, preset: str) -> List[str]:
    """Codec-aware quality flags. CRF/preset are NOT universal options."""
    codec = codec if codec in VALID_CODECS else "libx264"
    args: List[str] = ["-c:v", codec]

    if codec in ("libx264", "libx265"):
        args.extend(["-crf", str(crf), "-preset", preset])
    elif codec == "libvpx-vp9":
        args.extend(["-crf", str(crf), "-b:v", "0"])
    elif codec == "libaom-av1":
        args.extend(["-crf", str(crf), "-cpu-used", "4"])
    elif codec == "mpeg4":
        # mpeg4 has no CRF; map to qscale and drop preset
        try:
            qscale = max(2, min(31, round((float(crf) / 51) * 30) + 2))
        except (TypeError, ValueError):
            qscale = 5
        args.extend(["-qscale:v", str(qscale)])

    return args


def generate_ffmpeg_cmd(
    settings: Dict, input_file: str, output_base: str, thumbnail_path: Optional[str] = None
) -> List[Dict[str, str]]:
    """
    Generates a list of FFmpeg commands based on user settings.

    Returns a list of dicts:
    [
        {"cmd": "ffmpeg -i ... -y", "output_file": ".../out_1080p.mkv", "suffix": "1080p"},
        ...
    ]
    The command string always contains its own `-i <input>`; FFmpegProcess.start()
    must not add another one.
    """
    video_settings = settings.get("video", {})
    audio_settings = settings.get("audio", {})
    meta_settings = settings.get("metadata", {})

    crf = str(video_settings.get("crf", "23"))
    preset = video_settings.get("preset", "medium")
    if preset not in VALID_PRESETS:
        preset = "medium"
    codec = video_settings.get("codec", "libx264")
    resolutions = video_settings.get("resolution", ["1080p"])
    if isinstance(resolutions, str):
        resolutions = [resolutions]
    if not isinstance(resolutions, list):
        resolutions = ["1080p"]
    resolutions = list(
        dict.fromkeys(
            resolution
            for resolution in resolutions
            if resolution in {"1080p", "720p", "480p", "360p"}
        )
    ) or ["1080p"]

    remux = bool(video_settings.get("remux", False))
    output_as_video = bool(settings.get("output_as_video", False))

    audio_codec = audio_settings.get("codec", "aac")
    if audio_codec not in VALID_AUDIO_CODECS:
        audio_codec = "aac"
    audio_bitrate = audio_settings.get("bitrate", "128k")
    audio_track = str(audio_settings.get("track", "all")).lower()

    subtitle_mode = video_settings.get("subtitle_mode", "copy")
    if subtitle_mode not in ("copy", "drop"):
        subtitle_mode = "copy"

    sample_seconds = int(video_settings.get("sample_seconds", 0) or 0)

    trim_start = float(video_settings.get("trim_start", 0) or 0)
    trim_end = float(video_settings.get("trim_end", 0) or 0)

    wm_filter = "" if remux else generate_watermark_filter(settings)

    commands = []

    for res in resolutions:
        res_heights = {"1080p": 1080, "720p": 720, "480p": 480, "360p": 360}
        target_h = res_heights.get(res)

        cmd = [FFMPEG_BIN, "-i", input_file]

        if remux:
            # Fast container-fix mode: stream copy everything.
            cmd.extend(["-map", "0", "-c", "copy"])
        else:
            # The custom image is a Telegram delivery thumbnail, not an input
            # stream. Keeping it out of the encoded graph avoids MP4's lack of
            # a broadly compatible attached-PIC video codec and prevents the
            # normal video codec from overriding the thumbnail stream.
            # Never upscale: cap height at source height via min(ih, TARGET).
            scale_filter = ""
            if target_h:
                scale_filter = f"scale=-2:'min(ih,{target_h})'"

            video_filters = []
            is_complex = False
            if wm_filter:
                is_complex = "movie=" in wm_filter
                if is_complex:
                    if scale_filter:
                        wm_adjusted = wm_filter.replace("[0:v]", "[scaled]")
                        video_filters.append(f"{scale_filter} [scaled]; {wm_adjusted}")
                    else:
                        video_filters.append(wm_filter)
                else:
                    if scale_filter:
                        video_filters.append(scale_filter)
                    video_filters.append(wm_filter)
            elif scale_filter:
                video_filters.append(scale_filter)

            if not is_complex:
                cmd.extend(["-map", "0:v?"])

            # Audio track selection is 1-based in the UI; FFmpeg is 0-based.
            if audio_track in ("none", "off"):
                pass  # no audio mapping
            elif audio_track.isdigit():
                track_index = max(0, int(audio_track) - 1)
                cmd.extend(["-map", f"0:a:{track_index}?"])
            else:
                cmd.extend(["-map", "0:a?"])

            # Subtitles
            if subtitle_mode == "copy":
                cmd.extend(["-map", "0:s?"])

            if video_filters:
                if any("movie=" in f for f in video_filters):
                    cmd.extend(["-filter_complex", ",".join(video_filters)])
                else:
                    cmd.extend(["-vf", ",".join(video_filters)])

            cmd.extend(_video_codec_args(codec, crf, preset))

            if audio_track not in ("none", "off"):
                if audio_codec == "copy":
                    cmd.extend(["-c:a", "copy"])
                else:
                    cmd.extend(["-c:a", audio_codec, "-b:a", audio_bitrate])

            if subtitle_mode == "copy":
                # MP4 cannot carry every subtitle codec found in Matroska.
                # Convert text subtitles to the broadly supported mov_text
                # representation while preserving the user's copy/drop choice.
                cmd.extend(
                    ["-c:s", "mov_text" if output_as_video else "copy"]
                )
            else:
                cmd.extend(["-sn"])

            for key, value in meta_settings.get("global", {}).items():
                if value:
                    cmd.extend(["-metadata", f"{key}={value}"])
            for key, value in meta_settings.get("video", {}).items():
                if value:
                    cmd.extend(["-metadata:s:v", f"{key}={value}"])
            for key, value in meta_settings.get("audio", {}).items():
                if value:
                    cmd.extend(["-metadata:s:a", f"{key}={value}"])
            for key, value in meta_settings.get("subtitle", {}).items():
                if value:
                    cmd.extend(["-metadata:s:s", f"{key}={value}"])

        # Trim + sample: compute a single effective duration limit.
        # Output-side -ss keeps timestamps accurate; -t is relative to the seek.
        duration_limit = None
        if trim_end > trim_start > 0:
            duration_limit = trim_end - trim_start
        if sample_seconds > 0:
            duration_limit = (
                sample_seconds
                if duration_limit is None
                else min(duration_limit, sample_seconds)
            )

        if trim_start > 0:
            cmd.extend(["-ss", str(trim_start)])
        if duration_limit is not None:
            cmd.extend(["-t", str(duration_limit)])
        elif trim_end > 0:
            cmd.extend(["-to", str(trim_end)])

        cmd.extend(["-threads", str(FFMPEG_THREADS)])

        # Active custom FFmpeg args (user-selected override), appended before output.
        active_custom = settings.get("active_custom_ffmpeg")
        if active_custom and not remux:
            custom_map = settings.get("custom_ffmpeg", {}) or {}
            custom_args = custom_map.get(active_custom, "")
            if custom_args:
                if not validate_ffmpeg_command(custom_args):
                    log.warning(
                        "Ignoring unsafe legacy custom_ffmpeg args for %r",
                        active_custom,
                    )
                else:
                    cmd.extend(shlex.split(custom_args))

        # Stop writing before a runaway encode consumes the entire volume.
        cmd.extend(["-fs", str(MAX_OUTPUT_SIZE)])

        # Output path is appended by FFmpegProcess.start() along with -y.
        # Streamable delivery prefers MP4 + faststart for instant playback.
        suffix = f"_{res}" if len(resolutions) > 1 else ""
        if output_as_video:
            ext = ".mp4"
            # faststart moves the moov atom to the file head so Telegram can
            # stream the result immediately after upload, including remuxes.
            cmd.extend(["-movflags", "+faststart"])
        else:
            ext = ".mkv"
        # Avoid rare "queue full" failures on complex graphs.
        if not remux and "-max_muxing_queue_size" not in cmd:
            cmd.extend(["-max_muxing_queue_size", "4096"])
        output_path = f"{output_base}{suffix}{ext}"

        commands.append(
            {
                "cmd": shlex.join(cmd),
                "output_file": output_path,
                "suffix": res,
            }
        )

    return commands
