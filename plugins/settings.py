# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import os
import uuid
from html import escape
from string import Formatter

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

try:
    from pyrogram.errors.pyromod.listener_timeout import ListenerTimeout
except ImportError:
    from asyncio import TimeoutError as ListenerTimeout

from bot.config import THUMB_DIR, WATERMARK_DIR
from bot.decorator import is_banned, task
from bot.func.media import prepare_telegram_thumbnail
from bot.func.ffmpeg_utils import (
    VALID_AUDIO_CODECS,
    VALID_CODECS,
    VALID_PRESETS,
    _is_safe_asset_path,
    sanitize_custom_name,
    validate_ffmpeg_command,
)
from bot.logger import LOGGER
from bot.utils.listener import ListenerBusy, cancel_session, listen_once
from bot.utils.settings import (
    QUALITY_PRESETS,
    apply_quality_preset,
    normalize_settings,
)
from bot.utils.ui import (
    ICONS,
    back_btn,
    btn,
    close_btn,
    safe_edit,
    state_label,
    truncate,  # noqa: F401
)
from database import get_user_settings, is_user_tombstoned, update_user_settings

log = LOGGER(__name__)


def _owns_callback_message(callback_query) -> bool:
    return (
        getattr(getattr(callback_query, "message", None), "chat", None) is not None
        and callback_query.message.chat.id == callback_query.from_user.id
    )


async def _safe_callback_answer(callback_query, text=None, show_alert=False):
    """Answer immediately when possible; late listener replies must not crash."""
    try:
        await callback_query.answer(text, show_alert=show_alert)
    except Exception as exc:
        log.debug(f"Callback answer was no longer available: {exc}")


async def _edit_panel(message, text, reply_markup=None):
    """Edit text or caption panels consistently, including photo welcome cards."""
    if any(
        getattr(message, attribute, None)
        for attribute in ("photo", "video", "document", "audio", "animation")
    ):
        try:
            return await message.edit_caption(
                caption=text, reply_markup=reply_markup
            )
        except Exception:
            pass
    edited = await safe_edit(message, text, reply_markup)
    if edited:
        return edited
    if any(
        getattr(message, attribute, None)
        for attribute in ("video", "document", "audio", "animation")
    ):
        return await message.reply_text(text=text, reply_markup=reply_markup)
    return False


METADATA_KEYS = {
    "global": [
        "title", "artist", "album", "album_artist", "genre", "track", "disc", "date",
        "year", "comment", "description", "composer", "performer", "publisher",
        "encoder", "encoded_by", "lyrics", "synopsis", "copyright", "language",
        "creation_time", "software", "tool", "major_brand", "minor_version",
        "compatible_brands", "album-sort", "title-sort", "artist-sort",
        "composer-sort", "show", "season_number", "episode_sort", "network"
    ],
    "video": [
        "title", "language", "handler_name", "encoder", "creation_time", "rotate",
        "comment", "fps", "resolution", "bit_rate", "color_primaries",
        "color_space", "color_transfer"
    ],
    "audio": [
        "title", "artist", "album", "track", "genre", "language", "handler_name",
        "encoder", "creation_time", "comment", "lyrics", "composer",
        "album_artist", "publisher"
    ],
    "subtitle": [
        "title", "language", "handler_name", "encoder", "creation_time", "forced",
        "default", "track_name"
    ]
}


def _valid_rename_pattern(value: str) -> bool:
    allowed = {"original", "res", "codec", "date"}
    try:
        for _, field_name, format_spec, conversion in Formatter().parse(value):
            if field_name is not None and field_name not in allowed:
                return False
            # Format specs can request enormous expansions (for example
            # ``*>999999999``) before the later filename truncation runs.
            # Keep rename templates intentionally simple and bounded.
            if format_spec:
                return False
            if conversion not in (None, "s", "r", "a"):
                return False
        return True
    except ValueError:
        return False


@Client.on_message(filters.command(["settings", "u_setting"]) & filters.private)
@task
async def settings_command(client, message, query=False, user_id=None):
    target_user_id = user_id or message.from_user.id
    if await is_user_tombstoned(target_user_id):
        await message.reply_text("🗑 <b>Your bot data was deleted.</b>\n<i>Send /start to begin again.</i>")
        return
    await render_settings_menu(client, message, query=query, user_id=target_user_id)


async def render_settings_menu(client, message, query: bool = False, user_id: int = None):
    """Shared entry point for /settings and the cb_open_settings button."""
    if user_id is None:
        user_id = message.chat.id
    if await is_user_tombstoned(user_id):
        return

    settings = normalize_settings(await get_user_settings(user_id))
    video = settings["video"]
    audio = settings["audio"]
    resolutions = ", ".join(video["resolution"])
    profile = QUALITY_PRESETS.get(settings["profile"])
    profile_label = profile["label"] if profile else "🎛 Custom"
    output_label = "Video" if settings["output_as_video"] else "Document"
    sample = int(video["sample_seconds"])
    sample_label = f"{sample}s sample" if sample else "Full video"
    trim_start = float(video["trim_start"])
    trim_end = float(video["trim_end"])
    trim_label = (
        f"{trim_start:g}s → {trim_end:g}s"
        if trim_end > trim_start
        else "Off"
    )

    watermark = settings["watermark"]
    watermark_label = (
        f"{watermark['type'].title()} on" if watermark["enabled"] else "Off"
    )
    thumb = settings.get("thumbnail")
    thumbnail_ready = bool(thumb) and (
        isinstance(thumb, bytes)
        or (
            isinstance(thumb, str)
            and _is_safe_asset_path(thumb, THUMB_DIR)
            and os.path.isfile(thumb)
        )
    )
    thumbnail_label = "Set" if thumbnail_ready else "Not set"
    custom_active = settings.get("active_custom_ffmpeg") or "None"

    text = (
        f"{ICONS.settings} <b>Encoding settings</b>\n\n"
        f"<blockquote>{profile_label} · {escape(video['codec'])} · "
        f"CRF <code>{escape(video['crf'])}</code> · {escape(video['preset'])}\n"
        f"📐 {escape(resolutions)} · {escape(sample_label)} · "
        f"{'⚡ Remux' if video['remux'] else '🎞 Re-encode'}</blockquote>\n"
        f"<blockquote>{ICONS.audio} {escape(audio['codec'])} {escape(audio['bitrate'])} · "
        f"track {escape(audio['track'])}\n"
        f"💬 Subtitles: {escape(video['subtitle_mode'])} · "
        f"{'📤' if settings['output_as_video'] else ICONS.document} {output_label}</blockquote>\n"
        f"<blockquote>{ICONS.watermark} {watermark_label} · "
        f"{ICONS.thumb} {thumbnail_label} · ✂️ {trim_label}\n"
        f"🛠 Custom override: <code>{escape(custom_active)}</code></blockquote>\n"
        f"<i>Changes save instantly. Presets only alter encoding quality.</i>"
    )

    buttons = InlineKeyboardMarkup(
        [
            [btn(f"{ICONS.video} Video", "set_video"), btn(f"{ICONS.audio} Audio", "set_audio")],
            [
                btn(f"{ICONS.watermark} Watermark", "set_watermark"),
                btn(f"{ICONS.thumb} Thumbnail", "set_thumbnail"),
            ],
            [btn("📦 Output & more", "set_more"), btn(f"{ICONS.metadata} Metadata", "set_meta")],
            [btn(f"{ICONS.profile} Presets", "set_profiles"), btn("🛠 Advanced", "set_custom")],
            [btn(f"{ICONS.reset} Reset", "reset_confirm"), close_btn()],
        ]
    )

    if query:
        edited = await _edit_panel(message, text, buttons)
        if not edited and any(
            getattr(message, attribute, None)
            for attribute in ("video", "document", "audio", "animation")
        ):
            await message.reply_text(text=text, reply_markup=buttons)
    else:
        await message.reply_text(text=text, reply_markup=buttons)


@Client.on_callback_query(filters.regex(r"^(set_|use_profile_|reset_)") & filters.private)
async def settings_callback(client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id
    message = callback_query.message
    if not _owns_callback_message(callback_query):
        await _safe_callback_answer(callback_query, "This panel belongs to another chat.", show_alert=True)
        return
    if await is_banned(user_id):
        await _safe_callback_answer(callback_query, "You are banned from using this bot.", show_alert=True)
        return
    await _safe_callback_answer(callback_query)

    if data == "set_main":
        await _safe_callback_answer(callback_query)
        # Clear state when returning to main menu
        await render_settings_menu(client, message, query=True, user_id=user_id)
        return

    settings = normalize_settings(await get_user_settings(user_id))

    if data == "set_profiles":
        current = settings.get("profile", "custom")
        text = (
            f"{ICONS.profile} <b>Quality presets</b>\n\n"
            "<blockquote>Choose a starting point. Trim, resolutions, branding, "
            "metadata, and delivery settings stay unchanged.</blockquote>"
        )
        profile_rows = []
        for key, preset in QUALITY_PRESETS.items():
            profile_rows.append(
                [
                    btn(
                        state_label(f"{preset['label']} — {preset['description']}", key == current),
                        f"use_profile_{key}",
                    )
                ]
            )
        profile_rows.append([back_btn("set_main")])
        await _edit_panel(message, text, InlineKeyboardMarkup(profile_rows))

    elif data.startswith("use_profile_"):
        profile = data.replace("use_profile_", "", 1)
        if profile not in QUALITY_PRESETS:
            await _safe_callback_answer(callback_query, "Unknown preset.", show_alert=True)
            return
        settings = apply_quality_preset(settings, profile)
        saved = await update_user_settings(user_id, settings)
        await _safe_callback_answer(callback_query,
            f"{QUALITY_PRESETS[profile]['label']} applied"
            if saved
            else "⚠️ Could not save — try again.",
            show_alert=not saved,
        )
        if saved:
            await render_settings_menu(client, message, query=True, user_id=user_id)

    elif data == "reset_confirm":
        text = (
            f"{ICONS.warn} <b>Reset all settings?</b>\n\n"
            "<blockquote>This restores codec, audio, metadata, and delivery "
            "defaults and removes saved branding assets. It cannot be undone.</blockquote>"
        )
        await _edit_panel(
            message,
            text,
            InlineKeyboardMarkup(
                [
                    [btn("↺ Reset everything", "reset_execute")],
                    [back_btn("set_main")],
                ]
            ),
        )

    elif data == "reset_execute":
        settings = normalize_settings({})
        saved = await update_user_settings(user_id, settings)
        await _safe_callback_answer(callback_query,
            "↺ Defaults restored" if saved else "⚠️ Could not reset — try again.",
            show_alert=not saved,
        )
        if saved:
            for asset_path in (
                os.path.join(THUMB_DIR, f"{user_id}.jpg"),
                os.path.join(WATERMARK_DIR, f"{user_id}.png"),
                os.path.join(WATERMARK_DIR, "fonts", f"{user_id}.ttf"),
            ):
                try:
                    if os.path.isfile(asset_path):
                        os.remove(asset_path)
                except OSError:
                    pass
            await render_settings_menu(client, message, query=True, user_id=user_id)

    elif data == "set_video":
        video = settings["video"]
        sample = int(video["sample_seconds"])
        remux = bool(video["remux"])
        subtitle_mode = video["subtitle_mode"]
        resolution_count = len(video["resolution"])

        text = (
            f"{ICONS.video} <b>Video settings</b>\n\n"
            f"<blockquote>{escape(video['codec'])} · CRF <code>{escape(video['crf'])}</code> · "
            f"{escape(video['preset'])}\n"
            f"{resolution_count} output resolution(s) · "
            f"{'⚡ Remux' if remux else '🎞 Re-encode'}</blockquote>"
        )
        buttons = InlineKeyboardMarkup(
            [
                [btn(f"Codec: {video['codec']} ▸", "edit_video_codec")],
                [
                    btn(f"CRF: {video['crf']} ▸", "edit_video_crf"),
                    btn(f"Preset: {video['preset']} ▸", "edit_video_preset"),
                ],
                [btn(f"📐 Resolutions ({resolution_count}) ▸", "edit_video_res")],
                [
                    btn("⚡ Remux" if remux else "🎞 Re-encode", "toggle_video_remux"),
                    btn(f"⏱ Sample: {sample}s" if sample else "⏱ Full", "edit_video_sample"),
                ],
                [btn(f"💬 Subtitles: {subtitle_mode}", "toggle_video_subs")],
                [back_btn("set_main")],
            ]
        )
        await _edit_panel(message, text, buttons)

    elif data == "set_audio":
        audio_settings = settings["audio"]
        text = (
            f"{ICONS.audio} <b>Audio settings</b>\n\n"
            f"<blockquote>{escape(audio_settings['codec'])} · "
            f"{escape(audio_settings['bitrate'])} · track {escape(audio_settings['track'])}</blockquote>"
        )
        buttons = InlineKeyboardMarkup(
            [
                [btn(f"Bitrate: {audio_settings['bitrate']} ▸", "edit_audio_bitrate")],
                [
                    btn(f"Codec: {audio_settings['codec']} ▸", "edit_audio_codec"),
                    btn(f"Track: {audio_settings['track']} ▸", "edit_audio_track"),
                ],
                [back_btn("set_main")],
            ]
        )
        await _edit_panel(message, text, buttons)

    elif data == "set_more":
        video = settings["video"]
        trim_start = float(video["trim_start"])
        trim_end = float(video["trim_end"])
        rename = settings["rename"]["pattern"]
        as_video = bool(settings["output_as_video"])
        trim_label = (
            f"{trim_start:g}s → {trim_end:g}s"
            if trim_end > trim_start
            else "Off"
        )
        rename_label = truncate(rename, 22) if rename else "Off"
        compatibility_note = ""
        if as_video and (
            video["codec"] in {"libvpx-vp9", "libaom-av1", "mpeg4"}
            or video.get("remux")
            or settings["audio"]["codec"] == "ac3"
        ):
            compatibility_note = (
                "\n<i>⚠️ Some Telegram clients may not preview this streamable "
                "codec/container; the bot will fall back to a document if needed.</i>"
            )

        text = (
            "📦 <b>Output & more</b>\n\n"
            "<blockquote>Choose exactly what is encoded and how each result is named.</blockquote>"
            f"{compatibility_note}"
        )
        buttons = InlineKeyboardMarkup(
            [
                [btn(f"✂️ Trim: {trim_label}", "edit_trim")],
                [btn(f"✏️ Rename: {rename_label}", "edit_rename")],
                [
                    btn(
                        state_label(
                            "📤 Streamable video" if as_video else "📄 Document",
                            as_video,
                        ),
                        "toggle_output_mode",
                    )
                ],
                [back_btn("set_main")],
            ]
        )
        await _edit_panel(message, text, buttons)

    elif data == "set_meta":
        text = f"{ICONS.metadata} <b>Metadata</b>\n\nSelect a category to edit:"
        buttons = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🌐 Global", callback_data="set_meta_cat_global"),
                    InlineKeyboardButton("🎬 Video", callback_data="set_meta_cat_video"),
                ],
                [
                    InlineKeyboardButton("🎵 Audio", callback_data="set_meta_cat_audio"),
                    InlineKeyboardButton("💬 Subtitle", callback_data="set_meta_cat_subtitle"),
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="set_main")],
            ]
        )
        await _edit_panel(message, text, buttons)

    elif data == "set_custom":
        custom_cmds = settings.get("custom_ffmpeg", {})
        active = settings.get("active_custom_ffmpeg", "")
        text = (
            "🛠 <b>Advanced FFmpeg overrides</b>\n\n"
            "<blockquote>Only encoder and container flags are accepted. "
            "Input, output, protocols, and local file access stay bot-managed.</blockquote>\n"
        )
        if custom_cmds:
            text += f"{ICONS.success} Active: <code>{escape(active) if active else 'none'}</code>"
        else:
            text += "<i>No overrides saved yet.</i>"

        cmds_buttons = []
        for name, command in list(custom_cmds.items())[:10]:
            is_active = name == active
            use_label = "🔴 Disable" if is_active else f"▶️ Use {name}"
            use_callback = "toggle_active_custom" if is_active else f"use_custom_{name}"
            command_label = truncate(str(command), 34)
            text += (
                f"\n• <b>{escape(name)}</b>: <code>{escape(command_label)}</code>"
            )
            cmds_buttons.append(
                [
                    btn(use_label if is_active else "▶️ Use", use_callback),
                    btn("🗑 Delete", f"del_custom_{name}"),
                ]
            )
        cmds_buttons.append([btn(f"{ICONS.plus} Add override", "add_custom")])
        cmds_buttons.append([back_btn("set_main")])

        await _edit_panel(message, text, InlineKeyboardMarkup(cmds_buttons))

    elif data == "set_watermark":
        await _safe_callback_answer(callback_query)
        await watermark_callback(client, callback_query)
        return

    elif data == "set_thumbnail_upload":
        await _safe_callback_answer(callback_query, "Send a photo to continue…")
        await thumbnail_callback(client, callback_query)
    elif data.startswith("set_thumbnail"):
        await thumbnail_callback(client, callback_query)
        return

    elif data.startswith("set_meta_cat_"):
        category = data.replace("set_meta_cat_", "")
        page = 0
        if "|" in category:
            category, page = category.split("|")
            page = int(page)

        keys = METADATA_KEYS.get(category, [])
        total_keys = len(keys)
        per_page = 10
        total_pages = (total_keys + per_page - 1) // per_page

        start = page * per_page
        end = start + per_page
        current_keys = keys[start:end]

        text = f"📝 <b>{category.capitalize()} metadata</b>\n\n<i>Select a key to edit · page {page+1}/{total_pages}</i>"

        buttons = []
        # Create 2 columns
        row = []
        for key in current_keys:
            # Check if value is set
            val = settings.get("metadata", {}).get(category, {}).get(key, "")
            icon = "✏️" if val else "➕"
            label = f"{icon} {key}"

            row.append(InlineKeyboardButton(label, callback_data=f"edit_meta_val_{category}|{key}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        # Navigation
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"set_meta_cat_{category}|{page-1}"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"set_meta_cat_{category}|{page+1}"))

        if nav_row:
            buttons.append(nav_row)

        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="set_meta")])

        await _edit_panel(message, text, InlineKeyboardMarkup(buttons))



@Client.on_callback_query(
    filters.regex(r"^(toggle_video_remux|toggle_video_subs|toggle_output_mode)$")
    & filters.private
)
async def toggle_simple_callback(client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id
    if not _owns_callback_message(callback_query):
        await _safe_callback_answer(callback_query, "This panel belongs to another chat.", show_alert=True)
        return
    if await is_banned(user_id):
        await _safe_callback_answer(callback_query, "You are banned from using this bot.", show_alert=True)
        return
    await _safe_callback_answer(callback_query)

    settings = normalize_settings(await get_user_settings(user_id))
    v = settings.setdefault("video", {})

    if data == "toggle_video_remux":
        v["remux"] = not bool(v.get("remux", False))
        note = "⚡ Remux ON — streams are copied without re-encoding (watermark/scale/CRF ignored)."
        if not v["remux"]:
            note = "🎞 Re-encode restored."
        saved = await update_user_settings(user_id, settings)
        await _safe_callback_answer(callback_query,
            note[:200] if saved else "⚠️ Could not save — try again.",
            show_alert=not saved,
        )

    elif data == "toggle_video_subs":
        v["subtitle_mode"] = "drop" if v.get("subtitle_mode", "copy") == "copy" else "copy"
        note = (
            "💬 Subtitles will be dropped."
            if v["subtitle_mode"] == "drop"
            else "💬 Subtitles will be copied."
        )
        saved = await update_user_settings(user_id, settings)
        await _safe_callback_answer(callback_query,
            note[:200] if saved else "⚠️ Could not save — try again.",
            show_alert=not saved,
        )

    else:  # toggle_output_mode
        settings["output_as_video"] = not bool(settings.get("output_as_video", False))
        note = (
            "📤 Output will be sent as a streamable video."
            if settings["output_as_video"]
            else "📄 Output will be sent as a document."
        )
        saved = await update_user_settings(user_id, settings)
        await _safe_callback_answer(callback_query,
            note[:200] if saved else "⚠️ Could not save — try again.",
            show_alert=not saved,
        )

    callback_query.data = "set_video" if data != "toggle_output_mode" else "set_more"
    await settings_callback(client, callback_query)


@Client.on_callback_query(filters.regex("^edit_") & filters.private)
async def edit_callback(client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id
    message = callback_query.message
    if not _owns_callback_message(callback_query):
        await _safe_callback_answer(callback_query, "This panel belongs to another chat.", show_alert=True)
        return
    if await is_banned(user_id):
        await _safe_callback_answer(callback_query, "You are banned from using this bot.", show_alert=True)
        return
    await _safe_callback_answer(callback_query)

    settings = normalize_settings(await get_user_settings(user_id))

    if data == "edit_video_res":
        current_res = settings.get("video", {}).get("resolution", ["1080p"])
        if isinstance(current_res, str):
            current_res = [current_res]

        all_res = ["1080p", "720p", "480p", "360p"]
        buttons = [
            [
                btn(state_label(res, res in current_res, "✅"), f"toggle_res_{res}"),
                btn(
                    state_label(res, res in current_res, "✅"),
                    f"toggle_res_{all_res[index + 1]}",
                ),
            ]
            for index, res in enumerate(all_res[::2])
        ]
        buttons.append([back_btn("set_video")])

        await safe_edit(
            message,
            f"{ICONS.video} <b>Output resolutions</b>\n"
            f"<blockquote>{' · '.join(current_res)} selected. "
            "Every enabled variant will be delivered.</blockquote>",
            InlineKeyboardMarkup(buttons),
        )
        return

    prompts = {
        "edit_video_codec": f"<b>🎬 New video codec</b>\n<blockquote>Valid options:\n<code>"
        + "</code>, <code>".join(sorted(VALID_CODECS))
        + "</code></blockquote>\n<i>Type a value below, or press Cancel.</i>",
        "edit_video_crf": "<b>🎬 New CRF (0–51)</b>\n<blockquote>Lower = better quality, bigger file.\nDefault: <code>23</code></blockquote>\n<i>Type a value below, or press Cancel.</i>",
        "edit_video_preset": "<b>🎬 New preset</b>\n<blockquote><code>ultrafast</code> … <code>veryslow</code></blockquote>\n<i>Type a value below, or press Cancel.</i>",
        "edit_audio_bitrate": "<b>🎵 New audio bitrate</b>\n<blockquote>Range: <code>32k</code>–<code>512k</code><br>Examples: <code>128k</code> · <code>192k</code> · <code>320k</code></blockquote>\n<i>Type a value below, or press Cancel.</i>",
        "edit_audio_codec": "<b>🎵 New audio codec</b>\n<blockquote>Valid options:\n<code>"
        + "</code>, <code>".join(sorted(VALID_AUDIO_CODECS))
        + "</code></blockquote>\n<i>Type a value below, or press Cancel.</i>",
        "edit_audio_track": "<b>🎵 Audio track choice</b>\n<blockquote><code>all</code> / <code>none</code> / track number (e.g. <code>1</code>)</blockquote>\n<i>Type a value below, or press Cancel.</i>",
        "edit_video_sample": "<b>⏱ Sample encode length</b>\n<blockquote>Seconds: <code>0</code> = off, or <code>10</code>–<code>600</code>.\nEncodes only the first N seconds to test settings.</blockquote>\n<i>Type a value below, or press Cancel.</i>",
        "edit_trim": "<b>✂️ Trim window</b>\n<blockquote>Send <code>start end</code> in seconds, e.g. <code>10 120</code>.\nSend <code>off</code> to disable trimming.</blockquote>\n<i>Type a value below, or press Cancel.</i>",
        "edit_rename": "<b>✏️ Rename output files</b>\n<blockquote>Tokens: <code>{{original}}</code> <code>{{res}}</code> <code>{{codec}}</code> <code>{{date}}</code>\nExample: <code>{{original}} [{{res}}]</code>\nSend <code>off</code> to disable.</blockquote>\n<i>Type a value below, or press Cancel.</i>",
    }
    if data.startswith("edit_meta_val_"):
        category, key = data.split("_", 3)[-1].split("|")
        prompt = f"<b>1. Set value for <code>{escape(category)}</code> · <code>{escape(key)}</code></b>\n<i>Send the value, or <code>clear</code> to remove. Cancel to abort.</i>"
    else:
        prompt = prompts.get(data)

    if not prompt:
        await _safe_callback_answer(callback_query)
        return

    prompt_msg = None
    try:
        prompt_msg = await _edit_panel(
            message,
            prompt,
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Cancel", callback_data="cancel_input")]]
            ),
        )
        await _safe_callback_answer(callback_query, "Waiting for your input…")
        input_msg = await listen_once(
            client, user_id, prompt_msg.id, f"settings:{data}", 60
        )
        text = (input_msg.text or "").strip()
        try:
            await input_msg.delete()
        except Exception:
            pass
    except ListenerBusy as exc:
        await _safe_callback_answer(callback_query, str(exc), show_alert=True)
        return
    except ListenerTimeout:
        # Restore the settings menu instead of deleting the prompt.
        try:
            callback_query.data = "set_main"
            await settings_callback(client, callback_query)
        except Exception:
            if prompt_msg:
                try:
                    await prompt_msg.delete()
                except Exception:
                    pass
        return
    except Exception:
        if prompt_msg:
            try:
                await prompt_msg.delete()
            except Exception:
                pass
        callback_query.data = "set_main"
        await settings_callback(client, callback_query)
        return

    if not text:
        callback_query.data = "set_main"
        await settings_callback(client, callback_query)
        return

    settings = normalize_settings(await get_user_settings(user_id))

    async def invalid(msg: str):
        await message.reply_text(f"⚠️ {escape(msg[:180])}")
        await render_settings_menu(client, message, query=True, user_id=user_id)

    if data == "edit_video_codec":
        if text.strip() not in VALID_CODECS:
            await invalid("Choose one of: " + ", ".join(sorted(VALID_CODECS)))
            return
        settings.setdefault("video", {})["codec"] = text.strip()

    elif data == "edit_video_crf":
        if not text.isdigit() or not (0 <= int(text) <= 51):
            await invalid("Please enter a number between 0 and 51.")
            return
        settings.setdefault("video", {})["crf"] = text

    elif data == "edit_video_preset":
        if text.lower() not in VALID_PRESETS:
            await invalid("Choose from: " + ", ".join(VALID_PRESETS))
            return
        settings.setdefault("video", {})["preset"] = text.lower()

    elif data == "edit_audio_bitrate":
        value = text.lower()
        if (
            not value.endswith("k")
            or not value[:-1].isdigit()
            or not (32 <= int(value[:-1]) <= 512)
        ):
            await invalid("Choose a bitrate from 32k to 512k (for example 128k).")
            return
        settings.setdefault("audio", {})["bitrate"] = value

    elif data == "edit_audio_codec":
        if text.strip() not in VALID_AUDIO_CODECS:
            await invalid("Choose one of: " + ", ".join(sorted(VALID_AUDIO_CODECS)))
            return
        settings.setdefault("audio", {})["codec"] = text.strip()

    elif data == "edit_audio_track":
        val = text.strip().lower()
        if val not in ("all", "none") and not (val.isdigit() and int(val) >= 1):
            await invalid("Use all, none, or a 1-based track number like 1.")
            return
        settings.setdefault("audio", {})["track"] = val

    elif data == "edit_video_sample":
        val = text.strip()
        if val == "0":
            settings.setdefault("video", {})["sample_seconds"] = 0
        elif val.isdigit() and 10 <= int(val) <= 600:
            settings.setdefault("video", {})["sample_seconds"] = int(val)
        else:
            await invalid("Enter 0 (off) or a number of seconds between 10 and 600.")
            return

    elif data == "edit_trim":
        val = text.strip().lower()
        if val == "off":
            settings.setdefault("video", {}).update({"trim_start": 0, "trim_end": 0})
        else:
            parts = val.split()
            if len(parts) != 2:
                await invalid("Send two numbers: start end.")
                return
            try:
                t_start, t_end = float(parts[0]), float(parts[1])
            except ValueError:
                await invalid("Numbers only, e.g. 10 120.")
                return
            if t_end <= t_start or t_start < 0:
                await invalid("End must be greater than start.")
                return
            settings.setdefault("video", {}).update(
                {"trim_start": t_start, "trim_end": t_end}
            )

    elif data == "edit_rename":
        val = text.strip()
        if val.lower() == "off" or not val:
            settings["rename"] = {"pattern": ""}
        elif len(val) > 60:
            await invalid("Pattern too long (max 60 chars).")
            return
        else:
            if not _valid_rename_pattern(val):
                await invalid(
                    "Unknown placeholder. Allowed: {original} {res} {codec} {date}"
                )
                return
            settings["rename"] = {"pattern": val}

    elif data.startswith("edit_meta_val_"):
        category, key = data.split("_", 3)[-1].split("|")
        if len(text) > 500:
            await invalid("Metadata values are limited to 500 characters.")
            return

        if "metadata" not in settings:
            settings["metadata"] = {}
        if category not in settings["metadata"]:
            settings["metadata"][category] = {}

        if text.lower() == "clear":
            settings["metadata"][category].pop(key, None)
            change_text = f"🗑 Cleared {category} · {key}"
        else:
            settings["metadata"][category][key] = text
            change_text = f"✅ Set {category} · {key}"
        saved = await update_user_settings(user_id, settings)
        await _safe_callback_answer(
            callback_query,
            change_text if saved else "⚠️ Could not save — please try again.",
            show_alert=not saved,
        )
        if not saved:
            return
        callback_query.data = f"set_meta_cat_{category}"
        await settings_callback(client, callback_query)
        return

    if data.startswith(("edit_video_", "edit_audio_")):
        settings["profile"] = "custom"

    saved = await update_user_settings(user_id, settings)
    if saved:
        await _safe_callback_answer(callback_query, "✅ Setting saved")
    else:
        await _safe_callback_answer(callback_query, "⚠️ Could not save — please try again.", show_alert=True
        )

    await render_settings_menu(client, message, query=True, user_id=user_id)


@Client.on_callback_query(filters.regex("^toggle_res_") & filters.private)
async def toggle_res_callback(client, callback_query: CallbackQuery):
    res = callback_query.data.replace("toggle_res_", "")
    user_id = callback_query.from_user.id
    if not _owns_callback_message(callback_query):
        await _safe_callback_answer(callback_query, "This panel belongs to another chat.", show_alert=True)
        return
    if await is_banned(user_id):
        await _safe_callback_answer(callback_query, "You are banned from using this bot.", show_alert=True)
        return
    await _safe_callback_answer(callback_query)

    settings = normalize_settings(await get_user_settings(user_id))
    current_res = settings["video"]["resolution"]

    if res in current_res:
        if len(current_res) > 1:
            current_res.remove(res)
        else:
            await _safe_callback_answer(callback_query, "⚠️ At least one resolution must stay enabled.", show_alert=True)
            return
    else:
        current_res.append(res)

    settings["video"]["resolution"] = current_res
    saved = await update_user_settings(user_id, settings)
    await _safe_callback_answer(callback_query,
        "✅ Toggled" if saved else "⚠️ Could not save — try again.",
        show_alert=not saved,
    )
    if not saved:
        return

    callback_query.data = "edit_video_res"
    await edit_callback(client, callback_query)

@Client.on_callback_query(filters.regex("^add_custom") & filters.private)
async def add_custom_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    message = callback_query.message
    if not _owns_callback_message(callback_query):
        await _safe_callback_answer(callback_query, "This panel belongs to another chat.", show_alert=True)
        return
    if await is_banned(user_id):
        await _safe_callback_answer(callback_query, "You are banned from using this bot.", show_alert=True)
        return
    await _safe_callback_answer(callback_query)

    # 1. Ask for Name
    text = "<b>1. Name your custom command</b>\n<blockquote>Example: <code>my_1080p_preset</code></blockquote>\n<i>1–32 chars: letters, numbers, _ and -</i>"
    prompt_msg = await _edit_panel(
        message,
        text,
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cancel_input")]]),
    )
    await _safe_callback_answer(callback_query, "Waiting for your input…")

    try:
        name_msg = await listen_once(
            client, user_id, prompt_msg.id, "custom:name", 60
        )
        name = (name_msg.text or "").strip()
        try:
            await name_msg.delete()
        except Exception:
            pass
    except ListenerBusy as exc:
        await _safe_callback_answer(callback_query, str(exc), show_alert=True)
        return
    except ListenerTimeout:
        # Keep the menu; just toast the timeout.
        await _safe_callback_answer(callback_query, "⏰ Timed out — try again.", show_alert=True)
        try:
            callback_query.data = "set_custom"
            await settings_callback(client, callback_query)
        except Exception:
            if prompt_msg:
                try:
                    await prompt_msg.delete()
                except Exception:
                    pass
        return
    except Exception:
        # Cancelled
        callback_query.data = "set_custom"
        await settings_callback(client, callback_query)
        return

    if not name:
        await _safe_callback_answer(callback_query, "❌ Please send a text name.", show_alert=True)
        callback_query.data = "set_custom"
        await settings_callback(client, callback_query)
        return

    safe_name = sanitize_custom_name(name)
    if not safe_name:
        await _safe_callback_answer(callback_query,
            "❌ Use 1–32 chars: letters, numbers, _ and - only.",
            show_alert=True,
        )
        callback_query.data = "set_custom"
        await settings_callback(client, callback_query)
        return

    # 2. Ask for Command
    text = (
        f"<b>2. FFmpeg args for <code>{escape(safe_name)}</code></b>\n"
        "<blockquote>Example: <code>-crf 23 -preset fast</code></blockquote>\n"
        "<i>Use bounded quality/container flags. Codecs, stream mapping, paths, "
        "protocols, input, and output remain bot-managed.</i>"
    )
    await _edit_panel(
        prompt_msg,
        text,
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cancel_input")]]),
    )

    try:
        cmd_msg = await listen_once(
            client, user_id, message.id, "custom:command", 60
        )
        cmd = (cmd_msg.text or "").strip()
        try:
            await cmd_msg.delete()
        except Exception:
            pass
    except ListenerBusy as exc:
        await _safe_callback_answer(callback_query, str(exc), show_alert=True)
        return
    except ListenerTimeout:
        await _safe_callback_answer(callback_query, "⏰ Timed out — try again.", show_alert=True)
        try:
            callback_query.data = "set_custom"
            await settings_callback(client, callback_query)
        except Exception:
            if prompt_msg:
                try:
                    await prompt_msg.delete()
                except Exception:
                    pass
        return
    except Exception:
        # Cancelled
        callback_query.data = "set_custom"
        await settings_callback(client, callback_query)
        return

    # Validate before persisting. The allowlist prevents local-file and
    # protocol access while still allowing useful encoder/container knobs.
    if not validate_ffmpeg_command(cmd):
        await _safe_callback_answer(callback_query,
            "❌ Use only supported encoder/container flags; paths, protocols, "
            "input, and output are managed by the bot.",
            show_alert=True,
        )
        callback_query.data = "set_custom"
        await settings_callback(client, callback_query)
        return

    settings = normalize_settings(await get_user_settings(user_id))
    if "custom_ffmpeg" not in settings:
        settings["custom_ffmpeg"] = {}
    settings["custom_ffmpeg"][safe_name] = cmd
    saved = await update_user_settings(user_id, settings)

    if saved:
        await _safe_callback_answer(callback_query, f"✅ Custom command “{safe_name}” saved", show_alert=False
        )
    else:
        await _safe_callback_answer(callback_query, "⚠️ Could not save — try again.", show_alert=True
        )

    # Return to custom menu
    callback_query.data = "set_custom"
    await settings_callback(client, callback_query)


@Client.on_callback_query(filters.regex("^del_custom_") & filters.private)
async def del_custom_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    name = callback_query.data.replace("del_custom_", "")
    if not _owns_callback_message(callback_query):
        await _safe_callback_answer(callback_query, "This panel belongs to another chat.", show_alert=True)
        return
    if await is_banned(user_id):
        await _safe_callback_answer(callback_query, "You are banned from using this bot.", show_alert=True)
        return
    await _safe_callback_answer(callback_query)

    settings = normalize_settings(await get_user_settings(user_id))
    if name in settings["custom_ffmpeg"]:
        del settings["custom_ffmpeg"][name]
        if settings.get("active_custom_ffmpeg") == name:
            settings["active_custom_ffmpeg"] = ""
        saved = await update_user_settings(user_id, settings)
        if not saved:
            await _safe_callback_answer(callback_query, "⚠️ Could not delete — try again.", show_alert=True)
            return

    # Refresh custom menu
    callback_query.data = "set_custom"
    await settings_callback(client, callback_query)


@Client.on_callback_query(
    filters.regex(r"^(toggle_active_custom|use_custom_)") & filters.private
)
async def active_custom_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    data = callback_query.data
    if not _owns_callback_message(callback_query):
        await _safe_callback_answer(callback_query, "This panel belongs to another chat.", show_alert=True)
        return
    if await is_banned(user_id):
        await _safe_callback_answer(callback_query, "You are banned from using this bot.", show_alert=True)
        return
    await _safe_callback_answer(callback_query)
    settings = normalize_settings(await get_user_settings(user_id))
    custom = settings["custom_ffmpeg"]

    if data == "toggle_active_custom":
        settings["active_custom_ffmpeg"] = ""
        change_text = "🔴 Custom FFmpeg override off"
    else:
        name = data.replace("use_custom_", "")
        if name in custom:
            settings["active_custom_ffmpeg"] = name
            change_text = f"🟢 Using custom: {name}"
        else:
            await _safe_callback_answer(callback_query, "❌ Not found.", show_alert=True)
            return

    saved = await update_user_settings(user_id, settings)
    await _safe_callback_answer(
        callback_query,
        change_text if saved else "⚠️ Could not save — try again.",
        show_alert=not saved,
    )
    if not saved:
        return
    callback_query.data = "set_custom"
    await settings_callback(client, callback_query)


@Client.on_callback_query(
    filters.regex("^wm_(toggle|select|set|preview)") & filters.private
)
async def watermark_callback(client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id
    message = callback_query.message
    if not _owns_callback_message(callback_query):
        await _safe_callback_answer(callback_query, "This panel belongs to another chat.", show_alert=True)
        return
    if await is_banned(user_id):
        await _safe_callback_answer(callback_query, "You are banned from using this bot.", show_alert=True)
        return
    await _safe_callback_answer(callback_query)
    log.info(f"Watermark Callback: {data} for user {user_id}")

    settings = normalize_settings(await get_user_settings(user_id))
    wm = settings["watermark"]

    # Defaults
    if "enabled" not in wm: wm["enabled"] = False
    if "type" not in wm: wm["type"] = "text" # Default to text if enabled
    if "position" not in wm: wm["position"] = "top-right"
    if "opacity" not in wm: wm["opacity"] = "0.5"
    if "text" not in wm: wm["text"] = "Argons Encoder"
    if "font_size" not in wm: wm["font_size"] = "24"
    if "border_opacity" not in wm: wm["border_opacity"] = "0.5"
    if "timing_mode" not in wm: wm["timing_mode"] = "always"
    if "margins" not in wm: wm["margins"] = {"top": 10, "bottom": 10, "left": 10, "right": 10}

    if data == "set_watermark":
        status_icon = "🟢 On" if wm['enabled'] else "🔴 Off"

        text = (
            f"{ICONS.watermark} <b>Watermark</b>\n\n"
            f"<blockquote><b>Status:</b> {status_icon}\n"
            f"<b>Type:</b> {wm['type'].upper()}\n"
            f"<b>Position:</b> {wm['position'].replace('-', ' ').title()}\n"
            f"<b>Opacity:</b> {wm['opacity']}\n"
            f"<b>Timing:</b> {wm['timing_mode'].title()}</blockquote>"
        )

        if wm['type'] == 'text':
            has_font = (
                "✅ Custom"
                if os.path.isfile(os.path.join(WATERMARK_DIR, "fonts", f"{user_id}.ttf"))
                else "🤖 Default"
            )
            text += (
                f"📝 <b>Text Settings:</b>\n"
                f"<blockquote>• <b>Content:</b> <code>{escape(str(wm['text']))}</code>\n"
                f"• <b>Size:</b> {wm['font_size']}\n"
                f"• <b>Border Opacity:</b> {wm['border_opacity']}\n"
                f"• <b>Font:</b> {has_font}</blockquote>"
            )
        elif wm['type'] == 'image':
            has_img = (
                "✅ Uploaded"
                if wm.get("image_path") and os.path.isfile(str(wm.get("image_path")))
                else "❌ Not set"
            )
            text += f"🖼 <b>Image Settings:</b>\n<blockquote>• <b>File:</b> {has_img}\n• <b>Scale:</b> {wm.get('scale', '0.1')}</blockquote>"

        # Toggle Button
        toggle_text = "🔴 Disable" if wm['enabled'] else "🟢 Enable"
        toggle_btn = InlineKeyboardButton(toggle_text, callback_data="wm_toggle_enable")

        buttons = [
            [
                toggle_btn,
                InlineKeyboardButton(f"Type: {wm['type'].upper()}", callback_data="wm_select_type"),
            ],
            [
                InlineKeyboardButton(f"Pos: {wm['position'].title()}", callback_data="wm_select_pos"),
                InlineKeyboardButton(f"Opacity: {wm['opacity']}", callback_data="wm_edit_opacity"),
            ],
            [
                InlineKeyboardButton(f"Timing: {wm['timing_mode'].title()}", callback_data="wm_toggle_timing"),
                InlineKeyboardButton("📏 Margins", callback_data="wm_select_margins"),
            ],
        ]

        # Dynamic Buttons based on Type
        if wm['type'] == 'text':
            buttons.append([
                InlineKeyboardButton("✏️ Text", callback_data="wm_edit_text"),
                InlineKeyboardButton("📏 Size", callback_data="wm_edit_size"),
                InlineKeyboardButton("🔲 Border", callback_data="wm_edit_border_opacity"),
            ])
            buttons.append([InlineKeyboardButton("🔤 Custom Font", callback_data="wm_upload_font")])
        elif wm['type'] == 'image':
            buttons.append([
                InlineKeyboardButton("📤 Upload Image", callback_data="wm_upload_img"),
                InlineKeyboardButton("📐 Scale", callback_data="wm_edit_scale"),
            ])

        # Timing Config
        if wm['timing_mode'] != 'always':
            buttons.append([
                InlineKeyboardButton("⏱ Config Timing", callback_data="wm_config_timing"),
                InlineKeyboardButton("❓ Tutorial", callback_data="wm_timing_tutorial")
            ])

        buttons.append([
            InlineKeyboardButton("👁 Preview", callback_data="wm_preview"),
            InlineKeyboardButton("🔙 Back", callback_data="set_main")
        ])

        await _edit_panel(message, text, InlineKeyboardMarkup(buttons))

    elif data == "wm_toggle_enable":
        image_ready = bool(wm.get("image_path")) and os.path.isfile(
            str(wm.get("image_path"))
        )
        if not wm["enabled"] and wm["type"] == "image" and not image_ready:
            await _safe_callback_answer(callback_query,
                "Upload a watermark image before enabling it.", show_alert=True
            )
            return
        wm["enabled"] = not wm["enabled"]
        settings["watermark"]["enabled"] = wm["enabled"]
        saved = await update_user_settings(user_id, settings)
        await _safe_callback_answer(
            callback_query,
            "✅ Watermark updated" if saved else "⚠️ Could not save — try again.",
            show_alert=not saved,
        )
        if not saved:
            return

        callback_query.data = "set_watermark"
        await watermark_callback(client, callback_query)

    elif data == "wm_select_type":
        text = "<b>💧 Select watermark type:</b>"
        buttons = [
            [InlineKeyboardButton("📝 Text", callback_data="wm_set_type_text")],
            [InlineKeyboardButton("🖼 Image", callback_data="wm_set_type_image")],
            [InlineKeyboardButton("🔙 Back", callback_data="set_watermark")]
        ]
        await _edit_panel(message, text, InlineKeyboardMarkup(buttons))

    elif data.startswith("wm_set_type_"):
        new_type = data.split("_")[-1]
        if "watermark" not in settings: settings["watermark"] = {}
        settings["watermark"]["type"] = new_type
        saved = await update_user_settings(user_id, settings)
        await _safe_callback_answer(
            callback_query,
            "✅ Type Updated" if saved else "⚠️ Could not save — try again.",
            show_alert=not saved,
        )
        if not saved:
            return

        callback_query.data = "set_watermark"
        await watermark_callback(client, callback_query)

    elif data == "wm_select_pos":
        text = "<b>📐 Select watermark position:</b>"
        buttons = [
            [InlineKeyboardButton("↖️ Top-Left", callback_data="wm_set_pos_top-left"), InlineKeyboardButton("↗️ Top-Right", callback_data="wm_set_pos_top-right")],
            [InlineKeyboardButton("↙️ Bottom-Left", callback_data="wm_set_pos_bottom-left"), InlineKeyboardButton("↘️ Bottom-Right", callback_data="wm_set_pos_bottom-right")],
            [InlineKeyboardButton("🔙 Back", callback_data="set_watermark")]
        ]
        await _edit_panel(message, text, InlineKeyboardMarkup(buttons))

    elif data.startswith("wm_set_pos_"):
        new_pos = data.replace("wm_set_pos_", "")
        if "watermark" not in settings: settings["watermark"] = {}
        settings["watermark"]["position"] = new_pos
        saved = await update_user_settings(user_id, settings)
        if not saved:
            await _safe_callback_answer(callback_query, "⚠️ Could not save — try again.", show_alert=True)
            return

        callback_query.data = "set_watermark"
        await watermark_callback(client, callback_query)

    elif data == "wm_toggle_timing":
        modes = ["always", "range", "interval"]
        current = wm.get("timing_mode", "always")
        new_mode = modes[(modes.index(current) + 1) % len(modes)]

        if "watermark" not in settings: settings["watermark"] = {}
        settings["watermark"]["timing_mode"] = new_mode
        saved = await update_user_settings(user_id, settings)
        if not saved:
            await _safe_callback_answer(callback_query, "⚠️ Could not save — try again.", show_alert=True)
            return

        callback_query.data = "set_watermark"
        await watermark_callback(client, callback_query)

    elif data == "wm_select_margins":
        margins = wm.get("margins", {"top": 10, "bottom": 10, "left": 10, "right": 10})
        text = "<b>📏 Select margin to edit:</b>"
        buttons = [
            [
                InlineKeyboardButton(f"⬆️ Top: {margins.get('top', 10)}", callback_data="wm_edit_margin_top"),
                InlineKeyboardButton(f"⬇️ Bottom: {margins.get('bottom', 10)}", callback_data="wm_edit_margin_bottom")
            ],
            [
                InlineKeyboardButton(f"⬅️ Left: {margins.get('left', 10)}", callback_data="wm_edit_margin_left"),
                InlineKeyboardButton(f"➡️ Right: {margins.get('right', 10)}", callback_data="wm_edit_margin_right")
            ],
            [InlineKeyboardButton("🔙 Back", callback_data="set_watermark")]
        ]
        await _edit_panel(message, text, InlineKeyboardMarkup(buttons))

    elif data == "wm_preview":
        await _safe_callback_answer(callback_query, "Generating preview…")
        await message.reply_text("⏳ Generating preview…")

        from bot.func.preview import generate_preview
        preview_path = await generate_preview(user_id, settings)

        if preview_path and os.path.exists(preview_path):
            preview_thumb = None
            try:
                preview_thumb = await prepare_telegram_thumbnail(
                    preview_path,
                    destination=os.path.join(
                        THUMB_DIR, f"preview_{user_id}_{uuid.uuid4().hex[:8]}.jpg"
                    ),
                )
                keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🗑 Delete", callback_data="cb_close")]]
                )
                if preview_thumb:
                    await message.reply_photo(
                        photo=preview_thumb,
                        caption=f"{ICONS.watermark} <b>Watermark preview</b>",
                        reply_markup=keyboard,
                    )
                else:
                    await message.reply_document(
                        document=preview_path,
                        caption=f"{ICONS.watermark} <b>Watermark preview</b>",
                        reply_markup=keyboard,
                    )
            finally:
                for path in {preview_path, preview_thumb}:
                    if path:
                        try:
                            os.remove(path)
                        except OSError:
                            pass
        else:
            await _safe_callback_answer(callback_query,
                "❌ Preview failed — check your watermark settings.",
                show_alert=True,
            )


@Client.on_callback_query(filters.regex("^wm_edit_") & filters.private)
async def wm_edit_callback(client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id
    message = callback_query.message
    if not _owns_callback_message(callback_query):
        await _safe_callback_answer(callback_query, "This panel belongs to another chat.", show_alert=True)
        return
    if await is_banned(user_id):
        await _safe_callback_answer(callback_query, "You are banned from using this bot.", show_alert=True)
        return
    await _safe_callback_answer(callback_query)
    log.info(f"WM Edit Callback: {data} for user {user_id}")

    settings = normalize_settings(await get_user_settings(user_id))

    prompt = ""
    if data == "wm_edit_text":
        prompt = "<b>💧 Watermark text</b>\n<i>Type the text to burn into the video.</i>"
    elif data == "wm_edit_size":
        prompt = "<b>💧 Font size</b>\n<blockquote>Range: <code>10</code>–<code>100</code></blockquote>"
    elif data == "wm_edit_opacity":
        prompt = "<b>💧 Opacity</b>\n<blockquote>Range: <code>0.1</code>–<code>1.0</code></blockquote>"
    elif data == "wm_edit_border_opacity":
        prompt = "<b>💧 Border opacity</b>\n<blockquote><code>0.0</code> = invisible · <code>1.0</code> = solid</blockquote>"
    elif data == "wm_edit_scale":
        prompt = "<b>💧 Image scale</b>\n<blockquote>Relative to video width · <code>0.1</code>–<code>1.0</code></blockquote>"
    elif data.startswith("wm_edit_margin_"):
        side = data.split("_")[-1].title()
        prompt = f"<b>💧 {side} margin</b>\n<blockquote>Distance in pixels.</blockquote>"

    prompt_msg = await _edit_panel(
        message,
        prompt,
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cancel_input")]]),
    )
    await _safe_callback_answer(callback_query, "Waiting for your input…")

    try:
        input_msg = await listen_once(
            client, user_id, prompt_msg.id, f"settings:{data}", 60
        )
        text = (input_msg.text or "").strip()
        try:
            await input_msg.delete()
        except Exception:
            pass
        log.info(f"WM Edit Input: {text}")
    except ListenerBusy as exc:
        await _safe_callback_answer(callback_query, str(exc), show_alert=True)
        return
    except ListenerTimeout:
        await _safe_callback_answer(callback_query, "⏰ Timed out — try again.", show_alert=True)
        try:
            callback_query.data = "set_watermark"
            await watermark_callback(client, callback_query)
        except Exception:
            if prompt_msg:
                try:
                    await prompt_msg.delete()
                except Exception:
                    pass
        return
    except Exception:
        # Cancelled or other error
        callback_query.data = "set_watermark"
        await watermark_callback(client, callback_query)
        return

    async def reject_watermark(reason: str):
        await _safe_callback_answer(callback_query, reason, show_alert=True)
        callback_query.data = "set_watermark"
        await watermark_callback(client, callback_query)

    if not text:
        await reject_watermark("❌ Send a value, or press Cancel.")
        return

    settings = normalize_settings(await get_user_settings(user_id))
    if data == "wm_edit_text":
        if len(text) > 200:
            await reject_watermark("❌ Watermark text is limited to 200 characters.")
            return
        settings["watermark"]["text"] = text
    elif data == "wm_edit_size":
        if not text.isdigit() or not (10 <= int(text) <= 100):
            await reject_watermark("❌ Size must be 10–100.")
            return
        settings["watermark"]["font_size"] = text
    elif data == "wm_edit_opacity":
        try:
            val = float(text)
            if not (0.1 <= val <= 1.0): raise ValueError
        except ValueError:
            await reject_watermark("❌ Opacity must be 0.1–1.0.")
            return
        settings["watermark"]["opacity"] = text
    elif data == "wm_edit_border_opacity":
        try:
            val = float(text)
            if not (0.0 <= val <= 1.0): raise ValueError
        except ValueError:
            await reject_watermark("❌ Border opacity must be 0.0–1.0.")
            return
        settings["watermark"]["border_opacity"] = text
    elif data == "wm_edit_scale":
        try:
            val = float(text)
            if not (0.1 <= val <= 1.0): raise ValueError
        except ValueError:
            await reject_watermark("❌ Scale must be 0.1–1.0.")
            return
        settings["watermark"]["scale"] = text
    elif data.startswith("wm_edit_margin_"):
        side = data.split("_")[-1]
        if not text.isdigit() or not (0 <= int(text) <= 500):
            await reject_watermark("❌ Margin must be between 0 and 500 pixels.")
            return
        settings["watermark"]["margins"][side] = int(text)

        saved = await update_user_settings(user_id, settings)
        await _safe_callback_answer(
            callback_query,
            f"✅ {side.title()} margin updated" if saved else "⚠️ Could not save — try again.",
            show_alert=not saved,
        )
        if not saved:
            return

        # Return to margin menu
        callback_query.data = "wm_select_margins"
        await watermark_callback(client, callback_query)
        return

    saved = await update_user_settings(user_id, settings)
    await _safe_callback_answer(
        callback_query,
        "✅ Saved" if saved else "⚠️ Could not save — try again.",
        show_alert=not saved,
    )
    if not saved:
        return

    # Return
    callback_query.data = "set_watermark"
    await watermark_callback(client, callback_query)


@Client.on_callback_query(filters.regex("^wm_upload_img") & filters.private)
async def wm_upload_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    message = callback_query.message
    data = callback_query.data
    if not _owns_callback_message(callback_query):
        await _safe_callback_answer(callback_query, "This panel belongs to another chat.", show_alert=True)
        return
    if await is_banned(user_id):
        await _safe_callback_answer(callback_query, "You are banned from using this bot.", show_alert=True)
        return
    await _safe_callback_answer(callback_query)
    log.info(f"WM Upload Callback for user {user_id}")

    prompt_msg = await _edit_panel(
        message,
        "<b>📤 Send your watermark image</b>\n<blockquote>PNG or JPG · max 5 MB</blockquote>\n<i>Press Cancel to abort.</i>",
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cancel_input")]]),
    )
    await _safe_callback_answer(callback_query, "Waiting for your image…")

    try:
        input_msg = await listen_once(
            client, user_id, prompt_msg.id, f"settings:{data}", 60
        )
        if not input_msg.photo and not input_msg.document:
            await _safe_callback_answer(callback_query, "❌ Send a PNG or JPG image.", show_alert=True
            )
            callback_query.data = "set_watermark"
            await watermark_callback(client, callback_query)
            return

        doc = input_msg.document or input_msg.photo
        mime_type = str(getattr(doc, "mime_type", "") or "")
        file_name = str(getattr(doc, "file_name", "") or "")
        looks_like_image = mime_type.startswith("image/") or file_name.lower().endswith(
            (".png", ".jpg", ".jpeg", ".webp")
        )
        if not input_msg.photo and not looks_like_image:
            await _safe_callback_answer(callback_query, "❌ Send a PNG or JPG image.", show_alert=True
            )
            callback_query.data = "set_watermark"
            await watermark_callback(client, callback_query)
            return

        if getattr(doc, "file_size", 0) and doc.file_size > 5 * 1024 * 1024:
            await _safe_callback_answer(callback_query, "❌ Image too large (max 5 MB).", show_alert=True
            )
            callback_query.data = "set_watermark"
            await watermark_callback(client, callback_query)
            return

        os.makedirs(WATERMARK_DIR, exist_ok=True)
        final_path = os.path.join(WATERMARK_DIR, f"{user_id}.png")
        temp_path = os.path.join(
            WATERMARK_DIR, f"{user_id}_{uuid.uuid4().hex[:8]}.upload"
        )
        path = await input_msg.download(file_name=temp_path)
        try:
            await input_msg.delete()
        except Exception:
            pass
        if not path or not os.path.isfile(path):
            raise RuntimeError("image download did not produce a file")
        os.replace(path, final_path)

        settings = normalize_settings(await get_user_settings(user_id))
        settings["watermark"]["image_path"] = final_path
        settings["watermark"].pop("image_data", None)
        settings["watermark"]["scale"] = settings["watermark"].get("scale", 0.1)
        saved = await update_user_settings(user_id, settings)
        if not saved:
            try:
                os.remove(final_path)
            except OSError:
                pass

        await _safe_callback_answer(callback_query,
            "✅ Watermark image saved" if saved else "⚠️ Could not save — try again.",
            show_alert=not saved,
        )

    except ListenerBusy as exc:
        await _safe_callback_answer(callback_query, str(exc), show_alert=True)
        return
    except ListenerTimeout:
        await _safe_callback_answer(callback_query, "⏰ Timed out — try again.", show_alert=True)
        try:
            callback_query.data = "set_watermark"
            await watermark_callback(client, callback_query)
        except Exception:
            if prompt_msg:
                try:
                    await prompt_msg.delete()
                except Exception:
                    pass
        return
    except Exception as e:
        if "ListenerCanceled" in str(e) or isinstance(e, asyncio.CancelledError):
             callback_query.data = "set_watermark"
             await watermark_callback(client, callback_query)
             return
        log.error(f"WM Upload Error: {e}")
        temp_path = locals().get("temp_path")
        if temp_path and os.path.isfile(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        try:
            await prompt_msg.delete()
        except Exception:
            pass
        await message.reply_text("❌ Failed to save the image. Please try again.")
        return

    # Return
    callback_query.data = "set_watermark"
    await watermark_callback(client, callback_query)


@Client.on_callback_query(filters.regex("^wm_config_timing") & filters.private)
async def wm_timing_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    message = callback_query.message
    data = callback_query.data
    if not _owns_callback_message(callback_query):
        await _safe_callback_answer(callback_query, "This panel belongs to another chat.", show_alert=True)
        return
    if await is_banned(user_id):
        await _safe_callback_answer(callback_query, "You are banned from using this bot.", show_alert=True)
        return
    await _safe_callback_answer(callback_query)
    log.info(f"WM Timing Callback for user {user_id}")
    settings = normalize_settings(await get_user_settings(user_id))
    mode = settings["watermark"]["timing_mode"]

    prompt = ""
    if mode == "range":
        prompt = "<b>⏱ Range mode</b>\n<blockquote>Send <code>start end</code> in seconds, e.g. <code>10 60</code></blockquote>"
    elif mode == "interval":
        prompt = "<b>⏱ Interval mode</b>\n<blockquote>Send <code>duration period</code> — e.g. <code>5 30</code> shows for 5s every 30s.</blockquote>"
    else:
        await _safe_callback_answer(callback_query, "Mode is Always — no config needed.", show_alert=True)
        return

    prompt_msg = await _edit_panel(
        message,
        prompt,
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cancel_input")]]),
    )
    await _safe_callback_answer(callback_query, "Waiting for your input…")

    try:
        input_msg = await listen_once(
            client, user_id, prompt_msg.id, f"settings:{data}", 60
        )
        text = (input_msg.text or "").strip()
        try:
            await input_msg.delete()
        except Exception:
            pass

        async def reject_timing(reason: str):
            await _safe_callback_answer(callback_query, reason, show_alert=True)
            callback_query.data = "set_watermark"
            await watermark_callback(client, callback_query)

        settings = normalize_settings(await get_user_settings(user_id))
        parts = text.split() if text else []
        if len(parts) != 2:
            await reject_timing("❌ Format: two numbers (e.g. 10 60).")
            return
        try:
            v1, v2 = float(parts[0]), float(parts[1])
        except ValueError:
            await reject_timing("❌ Send two valid numbers.")
            return

        if mode == "range" and (v1 < 0 or v2 <= v1):
            await reject_timing("❌ Range end must be greater than start.")
            return
        if mode == "interval" and (v1 <= 0 or v2 <= 0 or v1 > v2):
            await reject_timing("❌ Duration must be positive and no longer than the period.")
            return

        if mode == "range":
            settings["watermark"]["start_time"] = v1
            settings["watermark"]["end_time"] = v2
        else:
            settings["watermark"]["interval_duration"] = v1
            settings["watermark"]["interval_period"] = v2

        saved = await update_user_settings(user_id, settings)
        await _safe_callback_answer(
            callback_query,
            "✅ Timing updated" if saved else "⚠️ Could not save — try again.",
            show_alert=not saved,
        )

    except ListenerBusy as exc:
        await _safe_callback_answer(callback_query, str(exc), show_alert=True)
        return
    except ListenerTimeout:
        await _safe_callback_answer(callback_query, "⏰ Timed out — try again.", show_alert=True)
        try:
            callback_query.data = "set_watermark"
            await watermark_callback(client, callback_query)
        except Exception:
            if prompt_msg:
                try:
                    await prompt_msg.delete()
                except Exception:
                    pass
        return
    except Exception:
        # Cancelled
        callback_query.data = "set_watermark"
        await watermark_callback(client, callback_query)
        return

    # Return
    callback_query.data = "set_watermark"
    await watermark_callback(client, callback_query)


@Client.on_callback_query(filters.regex("^wm_upload_font") & filters.private)
async def wm_upload_font_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    message = callback_query.message
    data = callback_query.data
    if not _owns_callback_message(callback_query):
        await _safe_callback_answer(callback_query, "This panel belongs to another chat.", show_alert=True)
        return
    if await is_banned(user_id):
        await _safe_callback_answer(callback_query, "You are banned from using this bot.", show_alert=True)
        return
    await _safe_callback_answer(callback_query)
    log.info(f"WM Font Upload Callback for user {user_id}")

    text = (
        "<b>🔤 Send your font file</b>\n"
        "<blockquote>TTF or OTF · max 5 MB</blockquote>\n"
        "<i>Press Cancel to abort.</i>"
    )
    await _safe_callback_answer(callback_query, "Send a font file to continue…")
    prompt_msg = await _edit_panel(
        message,
        text,
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cancel_input")]]),
    )

    try:
        input_msg = await listen_once(
            client, user_id, prompt_msg.id, f"settings:{data}", 60
        )
        file_name = (
            str(input_msg.document.file_name).lower()
            if input_msg.document and input_msg.document.file_name
            else ""
        )
        if not input_msg.document or not file_name.endswith((".ttf", ".otf")):
            await _safe_callback_answer(callback_query, "❌ Send a TTF or OTF font file.", show_alert=True
            )
            callback_query.data = "set_watermark"
            await watermark_callback(client, callback_query)
            return

        if int(getattr(input_msg.document, "file_size", 0) or 0) > 5 * 1024 * 1024:
            await _safe_callback_answer(callback_query, "❌ Font too large (max 5 MB).", show_alert=True
            )
            callback_query.data = "set_watermark"
            await watermark_callback(client, callback_query)
            return

        font_dir = os.path.join(WATERMARK_DIR, "fonts")
        os.makedirs(font_dir, exist_ok=True)
        final_font_path = os.path.join(font_dir, f"{user_id}.ttf")
        temp_font_path = os.path.join(
            font_dir, f"{user_id}_{uuid.uuid4().hex[:8]}.upload"
        )
        path = await input_msg.download(file_name=temp_font_path)
        try:
            await input_msg.delete()
        except Exception:
            pass
        if not path or not os.path.isfile(path):
            raise RuntimeError("font download did not produce a file")
        os.replace(path, final_font_path)

        settings = normalize_settings(await get_user_settings(user_id))
        settings["watermark"].pop("font_data", None)
        saved = await update_user_settings(user_id, settings)
        if not saved:
            try:
                os.remove(final_font_path)
            except OSError:
                pass

        if saved:
            await _safe_callback_answer(callback_query, "✅ Custom font saved", show_alert=False
            )
        else:
            await _safe_callback_answer(callback_query, "⚠️ Could not save — try again.", show_alert=True
            )

    except ListenerBusy as exc:
        await _safe_callback_answer(callback_query, str(exc), show_alert=True)
        return
    except ListenerTimeout:
        await _safe_callback_answer(callback_query, "⏰ Timed out — try again.", show_alert=True)
        try:
            callback_query.data = "set_watermark"
            await watermark_callback(client, callback_query)
        except Exception:
            if prompt_msg:
                try:
                    await prompt_msg.delete()
                except Exception:
                    pass
        return
    except Exception as e:
        if "ListenerCanceled" in str(e) or isinstance(e, asyncio.CancelledError):
             callback_query.data = "set_watermark"
             await watermark_callback(client, callback_query)
             return
        log.error(f"WM Font Upload Error: {e}")
        temp_font_path = locals().get("temp_font_path")
        if temp_font_path and os.path.isfile(temp_font_path):
            try:
                os.remove(temp_font_path)
            except OSError:
                pass
        try:
            await prompt_msg.delete()
        except Exception:
            pass
        await message.reply_text("❌ Failed to save the font. Please try again.")
        return

    # Return
    callback_query.data = "set_watermark"
    await watermark_callback(client, callback_query)


@Client.on_callback_query(
    filters.regex("^wm_timing_tutorial") & filters.private
)
async def wm_timing_tutorial_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if not _owns_callback_message(callback_query):
        await _safe_callback_answer(callback_query, "This panel belongs to another chat.", show_alert=True)
        return
    if await is_banned(user_id):
        await _safe_callback_answer(callback_query, "You are banned from using this bot.", show_alert=True)
        return
    await _safe_callback_answer(callback_query)
    text = "<b>1. Range mode</b>\n<blockquote>Show only between two times.\n• Format: <code>start end</code>\n• Example: <code>10 60</code> → visible from 10s to 60s</blockquote>\n\n<b>2. Interval mode</b>\n<blockquote>Flash periodically.\n• Format: <code>duration period</code>\n• Example: <code>5 30</code> → on for 5s, every 30s\n• Windows: 0–5s, 30–35s, 60–65s…</blockquote>\n\n<b>3. Always</b>\n<blockquote>Watermark stays visible the whole time.</blockquote>"
    await _edit_panel(
        callback_query.message,
        text,
        InlineKeyboardMarkup([[back_btn("set_watermark")]]),
    )


@Client.on_callback_query(filters.regex("^cancel_input") & filters.private)
async def cancel_input_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if not _owns_callback_message(callback_query):
        await _safe_callback_answer(callback_query, "This panel belongs to another chat.", show_alert=True)
        return
    cancelled = await cancel_session(
        client,
        user_id,
        message_id=callback_query.message.id,
    )
    if not cancelled:
        # The prompt edit and listener registration are adjacent awaits;
        # give a just-rendered prompt one event-loop turn to register.
        await asyncio.sleep(0.05)
        cancelled = await cancel_session(
            client,
            user_id,
            message_id=callback_query.message.id,
        )
    if cancelled:
        await _safe_callback_answer(callback_query, "🚫 Cancelled")
    else:
        await _safe_callback_answer(callback_query, "This prompt is no longer active.", show_alert=True
        )
        return

    current_text = (
        callback_query.message.text or callback_query.message.caption or ""
    )
    if "Upload thumbnail" in current_text:
        callback_query.data = "set_thumbnail"
        await thumbnail_callback(client, callback_query)


# Invoked via delegation from settings_callback (the ^set_ handler owns
# dispatch for all set_thumbnail* callback data).
async def thumbnail_callback(client, callback_query):
    user_id = callback_query.from_user.id
    settings = normalize_settings(await get_user_settings(user_id))
    thumb = settings.get("thumbnail")
    thumb_path_safe = isinstance(thumb, str) and _is_safe_asset_path(thumb, THUMB_DIR)
    thumb_ready = bool(thumb) and (
        isinstance(thumb, bytes)
        or (thumb_path_safe and os.path.isfile(str(thumb)))
    )
    data = callback_query.data

    if data == "set_thumbnail":
        status = "✅ Ready" if thumb_ready else "❌ Not set"
        text = (
            f"{ICONS.thumb} <b>Custom thumbnail</b>\n\n"
            f"<blockquote>Status: {status}</blockquote>"
            "Upload a square image for the best result. It is attached to every "
            "delivered Telegram variant; Telegram requires a fresh thumbnail "
            "upload for each media send."
        )
        buttons = []
        if thumb_ready:
            buttons.append(
                [
                    btn("👁 View", "set_thumbnail_view"),
                    btn("🗑 Delete", "set_thumbnail_delete"),
                ]
            )
        buttons.extend(
            [
                [btn("📤 Upload or replace", "set_thumbnail_upload")],
                [back_btn("set_main")],
            ]
        )
        await _edit_panel(
            callback_query.message, text, InlineKeyboardMarkup(buttons)
        )

    elif data == "set_thumbnail_view":
        if not thumb_ready:
            await _safe_callback_answer(callback_query, "No usable thumbnail is set.", show_alert=True)
            return
        temporary_path = None
        normalized_path = None
        try:
            if isinstance(thumb, bytes):
                temporary_path = os.path.join(
                    THUMB_DIR, f"legacy_{user_id}_{uuid.uuid4().hex[:8]}.jpg"
                )
                os.makedirs(THUMB_DIR, exist_ok=True)
                with open(temporary_path, "wb") as handle:
                    handle.write(thumb)
                source_path = temporary_path
            else:
                if not thumb_path_safe:
                    raise RuntimeError("thumbnail path is outside the managed asset directory")
                source_path = str(thumb)

            normalized_path = await prepare_telegram_thumbnail(
                source_path,
                destination=os.path.join(
                    THUMB_DIR, f"view_{user_id}_{uuid.uuid4().hex[:8]}.jpg"
                ),
            )
            caption = f"{ICONS.thumb} <b>Your current thumbnail</b>"
            if normalized_path:
                await client.send_photo(
                    chat_id=user_id,
                    photo=normalized_path,
                    caption=caption,
                )
            else:
                await client.send_document(
                    chat_id=user_id,
                    document=source_path,
                    caption=caption,
                )
            await _safe_callback_answer(callback_query)
        except Exception as exc:
            log.error(f"Failed to send thumbnail: {exc}")
            await _safe_callback_answer(callback_query, "Could not send thumbnail.", show_alert=True
            )
        finally:
            for path in (temporary_path, normalized_path):
                if path and os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    elif data == "set_thumbnail_delete":
        if not thumb:
            await _safe_callback_answer(callback_query, "No thumbnail to delete.", show_alert=True)
            return
        settings["thumbnail"] = None
        saved = await update_user_settings(user_id, settings)
        if saved and isinstance(thumb, str) and os.path.isfile(thumb):
            try:
                root = os.path.abspath(THUMB_DIR)
                target = os.path.abspath(thumb)
                if os.path.commonpath((root, target)) == root:
                    os.remove(target)
            except (OSError, ValueError) as exc:
                log.warning(f"Could not remove thumbnail asset: {exc}")
        await _safe_callback_answer(callback_query,
            "🗑 Thumbnail deleted" if saved else "⚠️ Could not delete — try again.",
            show_alert=not saved,
        )
        if saved:
            callback_query.data = "set_thumbnail"
            await thumbnail_callback(client, callback_query)

    elif data == "set_thumbnail_upload":
        prompt_msg = await _edit_panel(
            callback_query.message,
            f"{ICONS.thumb} <b>Upload thumbnail</b>\n\n"
            "<blockquote>JPG or PNG · maximum 5 MB</blockquote>"
            "<i>Send a photo in this chat, or press Cancel.</i>",
            InlineKeyboardMarkup([[back_btn("cancel_input")]]),
        )
        try:
            input_msg = await listen_once(
                client,
                user_id,
                prompt_msg.id,
                "thumbnail:upload",
                120,
                filters=filters.photo | filters.document,
            )
            photo = input_msg.photo or input_msg.document
            mime_type = str(getattr(photo, "mime_type", "") or "")
            photo_name = str(getattr(photo, "file_name", "") or "")
            if not input_msg.photo and not (
                mime_type.startswith("image/")
                or photo_name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
            ):
                await _safe_callback_answer(callback_query, "❌ Send a JPG, PNG, or WebP image.", show_alert=True
                )
                callback_query.data = "set_thumbnail"
                await thumbnail_callback(client, callback_query)
                return
            if int(getattr(photo, "file_size", 0) or 0) > 5 * 1024 * 1024:
                await _safe_callback_answer(callback_query,
                    "❌ Image too large (max 5 MB).",
                    show_alert=True,
                )
                callback_query.data = "set_thumbnail"
                await thumbnail_callback(client, callback_query)
                return

            os.makedirs(THUMB_DIR, exist_ok=True)
            final_path = os.path.join(THUMB_DIR, f"{user_id}.jpg")
            temp_path = os.path.join(
                THUMB_DIR, f"{user_id}_{uuid.uuid4().hex[:8]}.upload"
            )
            file_path = await client.download_media(input_msg, file_name=temp_path)
            if not file_path or not os.path.isfile(file_path):
                raise RuntimeError("thumbnail download did not produce a file")
            try:
                await input_msg.delete()
            except Exception:
                pass
            normalized_path = await prepare_telegram_thumbnail(
                file_path, destination=final_path
            )
            try:
                os.remove(file_path)
            except OSError:
                pass
            if not normalized_path:
                raise RuntimeError(
                    "thumbnail must be convertible to a square JPEG under 200 KB"
                )

            latest_settings = normalize_settings(await get_user_settings(user_id))
            latest_settings["thumbnail"] = final_path
            saved = await update_user_settings(user_id, latest_settings)
            if not saved:
                try:
                    os.remove(final_path)
                except OSError:
                    pass
            await _safe_callback_answer(callback_query,
                "✅ Thumbnail saved" if saved else "⚠️ Could not save — try again.",
                show_alert=not saved,
            )
            if saved:
                callback_query.data = "set_thumbnail"
                await thumbnail_callback(client, callback_query)
        except ListenerBusy as exc:
            await _safe_callback_answer(callback_query, str(exc), show_alert=True)
            return
        except ListenerTimeout:
            await _safe_callback_answer(callback_query, "⏰ Timed out — try again.", show_alert=True)
            callback_query.data = "set_thumbnail"
            await thumbnail_callback(client, callback_query)
        except Exception as exc:
            if "ListenerCanceled" in str(exc) or isinstance(exc, asyncio.CancelledError):
                return
            log.error(f"Thumbnail upload error: {exc}", exc_info=True)
            temp_path = locals().get("temp_path")
            if temp_path and os.path.isfile(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            await _safe_callback_answer(callback_query, "❌ Could not save thumbnail.", show_alert=True
            )
            callback_query.data = "set_thumbnail"
            await thumbnail_callback(client, callback_query)


