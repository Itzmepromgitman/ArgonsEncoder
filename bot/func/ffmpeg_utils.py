# Developed by ARGON telegram: @REACTIVEARGON
import os
import re
import shlex
from typing import Dict, List, Optional

from bot.config import FFMPEG_THREADS, FONT_PATH, WATERMARK_DIR
from bot.logger import LOGGER

log = LOGGER(__name__)

VALID_CODECS = {"libx264", "libx265", "libvpx-vp9", "libaom-av1", "mpeg4"}
VALID_PRESETS = [
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow",
]
VALID_AUDIO_CODECS = {"aac", "ac3", "copy"}


def validate_ffmpeg_command(cmd: str) -> bool:
    """
    Validates a custom FFmpeg command for safety.
    Blocks usage of -i (input) and -y (overwrite) as these are handled by the bot.
    """
    try:
        args = shlex.split(cmd)
        forbidden_flags = ["-i", "-y"]

        for arg in args:
            if arg in forbidden_flags:
                return False

        if not cmd.strip():
            return False

        return True
    except Exception:
        return False


def sanitize_custom_name(name: str) -> Optional[str]:
    """Whitelist custom command names so they are safe as Mongo keys and callback data."""
    name = (name or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name):
        return name
    return None


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
    """Restores the user's custom thumbnail to disk. Returns path or None."""
    thumb = settings.get("thumbnail")
    if not thumb or not isinstance(thumb, bytes):
        return None

    from bot.config import THUMB_DIR

    thumb_path = os.path.join(THUMB_DIR, f"{user_id}.jpg")

    if not os.path.exists(thumb_path):
        if not _write_asset(thumb_path, thumb):
            return None

    return thumb_path


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
    wm_type = wm_settings.get("type", "none")

    if not wm_settings.get("enabled", False) or wm_type == "none":
        return ""

    position = wm_settings.get("position", "top-right")
    opacity = float(wm_settings.get("opacity", 0.5))

    timing_mode = wm_settings.get("timing_mode", "always")
    start_time = float(wm_settings.get("start_time", 0))
    end_time = float(wm_settings.get("end_time", 0))
    interval_duration = float(wm_settings.get("interval_duration", 5))
    interval_period = float(wm_settings.get("interval_period", 30))

    enable_expr = ""
    if timing_mode == "range":
        enable_expr = f":enable='between(t,{start_time},{end_time})'"
    elif timing_mode == "interval":
        enable_expr = (
            f":enable='lt(mod(t,{interval_period}),{interval_duration})'"
        )

    margins = wm_settings.get("margins", {})
    m_top = margins.get("top", 10)
    m_bottom = margins.get("bottom", 10)
    m_left = margins.get("left", 10)
    m_right = margins.get("right", 10)

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
        credit_text = (
            ",drawtext=fontfile='bot/fonts/Roboto-Regular.ttf'"
            ":text='Developed by Argon':fontsize=24:fontcolor=black"
            ":x=W-tw-10:y=H-th-10"
        )

    if wm_type == "text":
        text = str(wm_settings.get("text", "Argons Encoder"))
        font_size = int(wm_settings.get("font_size", 24))
        border_opacity = float(wm_settings.get("border_opacity", 0.5))

        user_id = settings.get("user_id")
        font_path = FONT_PATH
        if user_id:
            custom_font = os.path.join(WATERMARK_DIR, "fonts", f"{user_id}.ttf")
            if os.path.exists(custom_font):
                font_path = custom_font

        safe_text = escape_drawtext(text)

        return (
            f"drawtext=fontfile='{font_path}':text='{safe_text}':fontsize={font_size}"
            f":fontcolor=white@{opacity}:x={x}:y={y}"
            f":box=1:boxcolor=black@{border_opacity}:boxborderw=5{enable_expr}"
            f"{credit_text}"
        )

    elif wm_type == "image":
        image_path = wm_settings.get("image_path", "")
        if not image_path or not os.path.exists(image_path):
            log.warning(f"Watermark image missing on disk: {image_path}")
            return ""

        scale = float(wm_settings.get("scale", 0.1))

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

    remux = bool(video_settings.get("remux", False))

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

        cmd = ["ffmpeg", "-i", input_file]

        if remux:
            # Fast container-fix mode: stream copy everything.
            cmd.extend(["-map", "0", "-c", "copy"])
        else:
            if thumbnail_path:
                cmd.extend(["-i", thumbnail_path])

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

            # Audio track selection
            if audio_track in ("none", "off"):
                pass  # no audio mapping
            elif audio_track.isdigit():
                cmd.extend(["-map", f"0:a:{audio_track}?"])
            else:
                cmd.extend(["-map", "0:a?"])

            # Subtitles
            if subtitle_mode == "copy":
                cmd.extend(["-map", "0:s?"])

            if thumbnail_path:
                cmd.extend(["-map", "1", "-c:v:1", "png"])
                cmd.extend(["-disposition:v:1", "attached_pic"])

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
                cmd.extend(["-c:s", "copy"])
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

        # Output path is appended by FFmpegProcess.start() along with -y.
        # Streamable delivery prefers MP4 + faststart for instant playback.
        suffix = f"_{res}" if len(resolutions) > 1 else ""
        if settings.get("output_as_video"):
            ext = ".mp4"
            # faststart moves the moov atom to the file head so Telegram can
            # stream the result immediately after upload.
            if not remux:
                cmd.extend(["-movflags", "+faststart"])
            elif remux:
                # remux into mp4 also benefits from faststart
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
