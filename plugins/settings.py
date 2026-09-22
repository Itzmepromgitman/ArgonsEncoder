# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import copy
import os
from html import escape

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

try:
    from pyrogram.errors.pyromod.listener_timeout import ListenerTimeout
except ImportError:
    from asyncio import TimeoutError as ListenerTimeout

from bot.decorator import task
from bot.func.ffmpeg_utils import (
    VALID_AUDIO_CODECS,
    VALID_CODECS,
    VALID_PRESETS,
    sanitize_custom_name,
    validate_ffmpeg_command,
)
from bot.logger import LOGGER
from bot.utils.ui import ICONS, back_btn, btn, close_btn, safe_edit, truncate  # noqa: F401
from database import get_user_settings, update_user_settings

log = LOGGER(__name__)

# Default Settings
DEFAULT_SETTINGS = {
    "video": {
        "crf": "23",
        "preset": "medium",
        "resolution": ["1080p"],
        "codec": "libx264",
        "subtitle_mode": "copy",   # copy | drop
        "sample_seconds": 0,       # 0 = off; else first N seconds only
        "remux": False,            # stream-copy container fix mode
        "trim_start": 0,
        "trim_end": 0,
    },
    "audio": {"bitrate": "128k", "codec": "aac", "track": "all"},
    "metadata": {
        "global": {"title": "Encoded by Argons", "author": "Argons Encoder"},
        "video": {},
        "audio": {},
        "subtitle": {},
    },
    "custom_ffmpeg": {},
    "rename": {"pattern": ""},
    "output_as_video": False,
}

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


@Client.on_message(filters.command(["settings", "u_setting"]))
@task
async def settings_command(client, message, query=False, user_id=None):
    await render_settings_menu(client, message, query=query, user_id=user_id or message.from_user.id)


async def render_settings_menu(client, message, query: bool = False, user_id: int = None):
    """Shared entry point for /settings and the cb_open_settings button."""
    if user_id is None:
        # Fallback for legacy callers: private chat id == user id.
        user_id = message.chat.id

    settings = await get_user_settings(user_id)

    # Merge with defaults if missing (deepcopy — never share the module dict)
    if not settings:
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        await update_user_settings(user_id, settings)

    # Ensure resolution is a list (migration)
    if "video" in settings and isinstance(settings["video"].get("resolution"), str):
        settings["video"]["resolution"] = [settings["video"]["resolution"]]
        await update_user_settings(user_id, settings)

    res_display = ", ".join(settings.get("video", {}).get("resolution", ["1080p"]))
    v = settings.get("video", {})
    a = settings.get("audio", {})

    remux_chip = "⚡ Remux" if v.get("remux") else "🎞 Re-encode"
    sample = int(v.get("sample_seconds", 0) or 0)
    sample_chip = f"⏱ Sample {sample}s" if sample else "🎞 Full"
    sub_mode = v.get("subtitle_mode", "copy")
    out_chip = "📤 As video" if settings.get("output_as_video") else "📄 As document"

    text = (
        f"{ICONS.settings} <b>Your encoding settings</b>\n\n"
        f"<blockquote>🎬 <code>{escape(str(v.get('codec', 'libx264')))}</code> · "
        f"CRF <code>{escape(str(v.get('crf', '23')))}</code> · "
        f"<code>{escape(str(v.get('preset', 'medium')))}</code>\n"
        f"📐 {escape(res_display)} · {remux_chip} · {sample_chip}\n"
        f"🎵 {escape(str(a.get('codec', 'aac')))} {escape(str(a.get('bitrate', '128k')))} · "
        f"track {escape(str(a.get('track', 'all')))}\n"
        f"💬 Subs: {escape(str(sub_mode))} · {out_chip}\n"
        f"📝 Title: <code>{escape(str(settings.get('metadata', {}).get('global', {}).get('title', 'N/A')))}</code></blockquote>\n"
        f"<i>Taps save instantly — no need to type /settings again.</i>"
    )

    buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(f"{ICONS.video} Video", callback_data="set_video"),
                InlineKeyboardButton(f"{ICONS.audio} Audio", callback_data="set_audio"),
            ],
            [
                InlineKeyboardButton(f"{ICONS.metadata} Metadata", callback_data="set_meta"),
                InlineKeyboardButton("🛠 Custom FFmpeg", callback_data="set_custom"),
            ],
            [
                InlineKeyboardButton(f"{ICONS.watermark} Watermark", callback_data="set_watermark"),
                InlineKeyboardButton(f"{ICONS.thumb} Thumbnail", callback_data="set_thumbnail"),
            ],
            [
                InlineKeyboardButton("➕ More", callback_data="set_more"),
            ],
            [close_btn()],
        ]
    )

    if query:
        await safe_edit(message, text, buttons)
    else:
        await message.reply_text(text=text, reply_markup=buttons)


@Client.on_callback_query(filters.regex("^set_"))
async def settings_callback(client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id
    message = callback_query.message

    if data == "set_main":
        # Clear state when returning to main menu
        await render_settings_menu(client, message, query=True, user_id=user_id)
        return

    settings = await get_user_settings(user_id)

    if data == "set_video":
        v = settings.get("video", {})
        sample = int(v.get("sample_seconds", 0) or 0)
        remux = bool(v.get("remux", False))
        sub_mode = v.get("subtitle_mode", "copy")

        text = f"{ICONS.video} <b>Video settings</b>\n\nSelect a parameter to edit:"
        buttons = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"Codec: {settings.get('video', {}).get('codec', 'libx264')} ▸",
                        callback_data="edit_video_codec",
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"CRF: {settings.get('video', {}).get('crf', '23')} ▸",
                        callback_data="edit_video_crf",
                    ),
                    InlineKeyboardButton(
                        f"Preset: {settings.get('video', {}).get('preset', 'medium')} ▸",
                        callback_data="edit_video_preset",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "Resolution (Multi) ▸", callback_data="edit_video_res"
                    )
                ],
                [
                    InlineKeyboardButton(
                        ("⚡ Remux: ON" if remux else "🎞 Re-encode"),
                        callback_data="toggle_video_remux",
                    ),
                    InlineKeyboardButton(
                        (f"⏱ Sample: {sample}s" if sample else "⏱ Sample: off"),
                        callback_data="edit_video_sample",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        f"💬 Subs: {sub_mode}",
                        callback_data="toggle_video_subs",
                    ),
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="set_main")],
            ]
        )
        await message.edit_text(text=text, reply_markup=buttons)

    elif data == "set_audio":
        a = settings.get("audio", {})
        text = f"{ICONS.audio} <b>Audio settings</b>\n\nSelect a parameter to edit:"
        buttons = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"Bitrate: {a.get('bitrate', '128k')} ▸",
                        callback_data="edit_audio_bitrate",
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"Codec: {a.get('codec', 'aac')} ▸",
                        callback_data="edit_audio_codec",
                    ),
                    InlineKeyboardButton(
                        f"Track: {a.get('track', 'all')} ▸",
                        callback_data="edit_audio_track",
                    ),
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="set_main")],
            ]
        )
        await message.edit_text(text=text, reply_markup=buttons)

    elif data == "set_more":
        trim = settings.get("video", {})
        t_start = float(trim.get("trim_start", 0) or 0)
        t_end = float(trim.get("trim_end", 0) or 0)
        rename = (settings.get("rename", {}) or {}).get("pattern", "")
        as_video = bool(settings.get("output_as_video", False))

        trim_chip = (
            f"{t_start:.0f}s → {t_end:.0f}s"
            if t_end > t_start > 0
            else (f"from {t_start:.0f}s" if t_start > 0 else "off")
        )

        text = "<b>➕ More options</b>\n\nFine-tune how your encodes behave:"
        buttons = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(f"✂️ Trim: {trim_chip}", callback_data="edit_trim"),
                ],
                [
                    InlineKeyboardButton(
                        f"✏️ Rename: {(rename[:14] + '…') if len(rename) > 16 else (rename or 'off')}",
                        callback_data="edit_rename",
                    )
                ],
                [
                    InlineKeyboardButton(
                        ("📤 Output: video" if as_video else "📄 Output: document"),
                        callback_data="toggle_output_mode",
                    )
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="set_main")],
            ]
        )
        await message.edit_text(text=text, reply_markup=buttons)

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
        await message.edit_text(text=text, reply_markup=buttons)

    elif data == "set_custom":
        custom_cmds = settings.get("custom_ffmpeg", {})
        text = "<b>🛠 Custom FFmpeg commands</b>\n\n<blockquote>Saved commands:</blockquote>\n"

        cmds_buttons = []
        if custom_cmds:
            for name, cmd in custom_cmds.items():
                text += f"• <b>{escape(str(name))}</b>: <code>{escape(str(cmd))}</code>\n"
                cmds_buttons.append(
                    [
                        InlineKeyboardButton(
                            f"🗑 Delete {name}", callback_data=f"del_custom_{name}"
                        )
                    ]
                )
        else:
            text += "<i>No custom commands saved yet.</i>"

        cmds_buttons.append(
            [InlineKeyboardButton("➕ Add New Command", callback_data="add_custom")]
        )
        # Active override toggle (wired into generate_ffmpeg_cmd).
        active = settings.get("active_custom_ffmpeg")
        if custom_cmds:
            if active and active in custom_cmds:
                cmds_buttons.append(
                    [
                        InlineKeyboardButton(
                            f"🟢 Active: {active}",
                            callback_data="toggle_active_custom",
                        )
                    ]
                )
            else:
                first = next(iter(custom_cmds))
                cmds_buttons.append(
                    [
                        InlineKeyboardButton(
                            f"🔴 Activate {first}",
                            callback_data=f"use_custom_{first}",
                        )
                    ]
                )
        cmds_buttons.append([InlineKeyboardButton("🔙 Back", callback_data="set_main")])

        await message.edit_text(
            text=text, reply_markup=InlineKeyboardMarkup(cmds_buttons)
        )

    elif data == "set_watermark":
        await watermark_callback(client, callback_query)
        return

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

        await message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(buttons))



@Client.on_callback_query(filters.regex(r"^(toggle_video_remux|toggle_video_subs|toggle_output_mode)$"))
async def toggle_simple_callback(client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id

    settings = await get_user_settings(user_id)
    if not settings:
        settings = copy.deepcopy(DEFAULT_SETTINGS)
    v = settings.setdefault("video", {})

    if data == "toggle_video_remux":
        v["remux"] = not bool(v.get("remux", False))
        note = "⚡ Remux ON — streams are copied without re-encoding (watermark/scale/CRF ignored)."
        if not v["remux"]:
            note = "🎞 Re-encode restored."
        await update_user_settings(user_id, settings)
        await callback_query.answer("✅ Updated")

    elif data == "toggle_video_subs":
        v["subtitle_mode"] = "drop" if v.get("subtitle_mode", "copy") == "copy" else "copy"
        note = (
            "💬 Subtitles will be dropped."
            if v["subtitle_mode"] == "drop"
            else "💬 Subtitles will be copied."
        )
        await update_user_settings(user_id, settings)
        await callback_query.answer("✅ Updated")

    else:  # toggle_output_mode
        settings["output_as_video"] = not bool(settings.get("output_as_video", False))
        note = (
            "📤 Output will be sent as a streamable video."
            if settings["output_as_video"]
            else "📄 Output will be sent as a document."
        )
        await update_user_settings(user_id, settings)
        await callback_query.answer("✅ Updated")

    callback_query.data = "set_video" if data != "toggle_output_mode" else "set_more"
    await settings_callback(client, callback_query)


@Client.on_callback_query(filters.regex("^edit_"))
async def edit_callback(client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id
    message = callback_query.message

    settings = await get_user_settings(user_id)
    if not settings:
        settings = copy.deepcopy(DEFAULT_SETTINGS)

    if data == "edit_video_res":
        current_res = settings.get("video", {}).get("resolution", ["1080p"])
        if isinstance(current_res, str):
            current_res = [current_res]

        all_res = ["1080p", "720p", "480p", "360p"]

        buttons = []
        for res in all_res:
            icon = "✅" if res in current_res else "❌"
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"{icon} {res}", callback_data=f"toggle_res_{res}"
                    )
                ]
            )

        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="set_video")])

        await safe_edit(
            message,
            "<b>📐 Resolutions</b>\n<i>Tap to toggle — pick one or more.</i>",
            InlineKeyboardMarkup(buttons),
        )
        return

    prompts = {
        "edit_video_codec": f"<b>🎬 New video codec</b>\n<blockquote>Valid options:\n<code>"
        + "</code>, <code>".join(sorted(VALID_CODECS))
        + "</code></blockquote>\n<i>Type a value below, or press Cancel.</i>",
        "edit_video_crf": "<b>🎬 New CRF (0–51)</b>\n<blockquote>Lower = better quality, bigger file.\nDefault: <code>23</code></blockquote>\n<i>Type a value below, or press Cancel.</i>",
        "edit_video_preset": "<b>🎬 New preset</b>\n<blockquote><code>ultrafast</code> … <code>veryslow</code></blockquote>\n<i>Type a value below, or press Cancel.</i>",
        "edit_audio_bitrate": "<b>🎵 New audio bitrate</b>\n<blockquote>Examples: <code>128k</code>, <code>192k</code>, <code>320k</code></blockquote>\n<i>Type a value below, or press Cancel.</i>",
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
        await callback_query.answer()
        return

    prompt_msg = None
    try:
        prompt_msg = await message.edit_text(
            text=prompt,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Cancel", callback_data="cancel_input")]]
            ),
        )
        input_msg = await client.listen(chat_id=user_id, timeout=60)
        text = input_msg.text
        await input_msg.delete()
    except ListenerTimeout:
        # Restore the settings menu instead of deleting the prompt.
        await callback_query.answer("⏰ Timed out.", show_alert=False)
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
        await callback_query.answer("❌ Please send text (or press Cancel).", show_alert=True)
        return

    async def invalid(msg: str):
        await callback_query.answer(f"❌ {msg[:180]}", show_alert=True)
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
        if not text.endswith("k") or not text[:-1].isdigit():
            await invalid("Format: 128k, 192k, etc.")
            return
        settings.setdefault("audio", {})["bitrate"] = text

    elif data == "edit_audio_codec":
        if text.strip() not in VALID_AUDIO_CODECS:
            await invalid("Choose one of: " + ", ".join(sorted(VALID_AUDIO_CODECS)))
            return
        settings.setdefault("audio", {})["codec"] = text.strip()

    elif data == "edit_audio_track":
        val = text.strip().lower()
        if val not in ("all", "none") and not val.isdigit():
            await invalid("Use: all / none / a track number like 1.")
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
            # Reject patterns that would explode at format time.
            try:
                val.format(original="", res="", codec="", date="")
            except (KeyError, IndexError, ValueError):
                await invalid(
                    "Unknown placeholder. Allowed: {original} {res} {codec} {date}"
                )
                return
            settings["rename"] = {"pattern": val}

    elif data.startswith("edit_meta_val_"):
        category, key = data.split("_", 3)[-1].split("|")

        if "metadata" not in settings:
            settings["metadata"] = {}
        if category not in settings["metadata"]:
            settings["metadata"][category] = {}

        if text.lower() == "clear":
            settings["metadata"][category].pop(key, None)
            await callback_query.answer(f"🗑 Cleared {category} · {key}", show_alert=False)
        else:
            settings["metadata"][category][key] = text
            await callback_query.answer(
                f"✅ Set {category} · {key}", show_alert=False
            )
        await update_user_settings(user_id, settings)
        callback_query.data = f"set_meta_cat_{category}"
        await settings_callback(client, callback_query)
        return

    saved = await update_user_settings(user_id, settings)
    if saved:
        await callback_query.answer("✅ Setting saved", show_alert=False)
    else:
        await callback_query.answer(
            "⚠️ Could not save — please try again.", show_alert=True
        )

    await render_settings_menu(client, message, query=True, user_id=user_id)


@Client.on_callback_query(filters.regex("^toggle_res_"))
async def toggle_res_callback(client, callback_query: CallbackQuery):
    res = callback_query.data.replace("toggle_res_", "")
    user_id = callback_query.from_user.id

    settings = await get_user_settings(user_id)
    if "video" not in settings:
        settings["video"] = {}

    current_res = settings["video"].get("resolution", ["1080p"])
    if isinstance(current_res, str):
        current_res = [current_res]

    if res in current_res:
        if len(current_res) > 1:
            current_res.remove(res)
        else:
            await callback_query.answer("⚠️ At least one resolution must stay enabled.", show_alert=True)
            return
    else:
        current_res.append(res)

    settings["video"]["resolution"] = current_res
    await update_user_settings(user_id, settings)
    await callback_query.answer("✅ Toggled")

    callback_query.data = "edit_video_res"
    await edit_callback(client, callback_query)

@Client.on_callback_query(filters.regex("^add_custom"))
async def add_custom_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    message = callback_query.message

    # 1. Ask for Name
    text = "<b>1. Name your custom command</b>\n<blockquote>Example: <code>my_1080p_preset</code></blockquote>\n<i>1–32 chars: letters, numbers, _ and -</i>"
    prompt_msg = await message.edit_text(text=text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cancel_input")]]))

    try:
        name_msg = await client.listen(chat_id=user_id, timeout=60)
        name = name_msg.text
        await name_msg.delete()
    except ListenerTimeout:
        # Keep the menu; just toast the timeout.
        await callback_query.answer("⏰ Timed out — try again.", show_alert=True)
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
        await callback_query.answer("❌ Please send a text name.", show_alert=True)
        return

    safe_name = sanitize_custom_name(name)
    if not safe_name:
        await callback_query.answer(
            "❌ Use 1–32 chars: letters, numbers, _ and - only.", show_alert=True
        )
        return

    # 2. Ask for Command
    text = f"<b>2. FFmpeg args for <code>{escape(safe_name)}</code></b>\n<blockquote>Example: <code>-c:v libx264 -crf 23</code></blockquote>\n<i>Input (<code>-i</code>) and overwrite (<code>-y</code>) are managed by the bot.</i>"
    await prompt_msg.edit_text(text=text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cancel_input")]]))

    try:
        cmd_msg = await client.listen(chat_id=user_id, timeout=60)
        cmd = cmd_msg.text
        await cmd_msg.delete()
    except ListenerTimeout:
        await callback_query.answer("⏰ Timed out — try again.", show_alert=True)
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

    # Validate
    if not validate_ffmpeg_command(cmd):
        await callback_query.answer(
            "❌ Invalid command: forbidden flags (-i, -y) or empty.", show_alert=True
        )
        return

    settings = await get_user_settings(user_id)
    if "custom_ffmpeg" not in settings:
        settings["custom_ffmpeg"] = {}
    settings["custom_ffmpeg"][safe_name] = cmd
    saved = await update_user_settings(user_id, settings)

    if saved:
        await callback_query.answer(f"✅ Custom command “{safe_name}” saved", show_alert=False)
    else:
        await callback_query.answer("⚠️ Could not save — try again.", show_alert=True)

    # Return to custom menu
    callback_query.data = "set_custom"
    await settings_callback(client, callback_query)


@Client.on_callback_query(filters.regex("^del_custom_"))
async def del_custom_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    name = callback_query.data.replace("del_custom_", "")

    settings = await get_user_settings(user_id)
    if "custom_ffmpeg" in settings and name in settings["custom_ffmpeg"]:
        del settings["custom_ffmpeg"][name]
        if settings.get("active_custom_ffmpeg") == name:
            settings["active_custom_ffmpeg"] = ""
            settings["active_custom_ffmpeg"] = ""
        await update_user_settings(user_id, settings)

    # Refresh custom menu
    callback_query.data = "set_custom"
    await settings_callback(client, callback_query)


@Client.on_callback_query(filters.regex(r"^(toggle_active_custom|use_custom_)"))
async def active_custom_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    data = callback_query.data
    settings = await get_user_settings(user_id)
    custom = settings.get("custom_ffmpeg", {}) or {}

    if data == "toggle_active_custom":
        settings["active_custom_ffmpeg"] = ""
        await callback_query.answer("🔴 Custom FFmpeg override off")
    else:
        name = data.replace("use_custom_", "")
        if name in custom:
            settings["active_custom_ffmpeg"] = name
            await callback_query.answer(f"🟢 Using custom: {name}")
        else:
            await callback_query.answer("❌ Not found.", show_alert=True)
            return

    await update_user_settings(user_id, settings)
    callback_query.data = "set_custom"
    await settings_callback(client, callback_query)


@Client.on_callback_query(filters.regex("^wm_(toggle|select|set|preview)"))
async def watermark_callback(client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id
    message = callback_query.message
    log.info(f"Watermark Callback: {data} for user {user_id}")

    settings = await get_user_settings(user_id)
    wm = settings.get("watermark", {})

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
            has_font = "✅ Custom" if os.path.exists(f"watermarks/fonts/{user_id}.ttf") else "🤖 Default"
            text += (
                f"📝 <b>Text Settings:</b>\n"
                f"<blockquote>• <b>Content:</b> <code>{escape(str(wm['text']))}</code>\n"
                f"• <b>Size:</b> {wm['font_size']}\n"
                f"• <b>Border Opacity:</b> {wm['border_opacity']}\n"
                f"• <b>Font:</b> {has_font}</blockquote>"
            )
        elif wm['type'] == 'image':
            has_img = "✅ Uploaded" if wm.get("image_path") else "❌ Not Set"
            text += f"🖼 <b>Image Settings:</b>\n<blockquote>• <b>File:</b> {has_img}\n• <b>Scale:</b> {wm.get('scale', '0.1')}</blockquote>"

        # Toggle Button
        toggle_text = "🔴 Disable" if wm['enabled'] else "🟢 Enable"
        toggle_btn = InlineKeyboardButton(toggle_text, callback_data="wm_toggle_enable")

        buttons = [
            [
                toggle_btn,
                InlineKeyboardButton(f"Type: {wm['type'].upper()}", callback_data="wm_select_type"),
                InlineKeyboardButton(f"Pos: {wm['position'].title()}", callback_data="wm_select_pos"),
            ],
            [
                InlineKeyboardButton(f"Opacity: {wm['opacity']}", callback_data="wm_edit_opacity"),
                InlineKeyboardButton(f"Timing: {wm['timing_mode'].title()}", callback_data="wm_toggle_timing"),
                InlineKeyboardButton("📏 Margins", callback_data="wm_select_margins")
            ]
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

        await message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "wm_toggle_enable":
        wm['enabled'] = not wm['enabled']
        if "watermark" not in settings: settings["watermark"] = {}
        settings["watermark"]["enabled"] = wm['enabled']
        await update_user_settings(user_id, settings)
        await callback_query.answer("✅ Updated", show_alert=False)

        callback_query.data = "set_watermark"
        await watermark_callback(client, callback_query)

    elif data == "wm_select_type":
        text = "<b>💧 Select watermark type:</b>"
        buttons = [
            [InlineKeyboardButton("📝 Text", callback_data="wm_set_type_text")],
            [InlineKeyboardButton("🖼 Image", callback_data="wm_set_type_image")],
            [InlineKeyboardButton("🔙 Back", callback_data="set_watermark")]
        ]
        await message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("wm_set_type_"):
        new_type = data.split("_")[-1]
        if "watermark" not in settings: settings["watermark"] = {}
        settings["watermark"]["type"] = new_type
        await update_user_settings(user_id, settings)
        await callback_query.answer("✅ Type Updated", show_alert=False)

        callback_query.data = "set_watermark"
        await watermark_callback(client, callback_query)

    elif data == "wm_select_pos":
        text = "<b>📐 Select watermark position:</b>"
        buttons = [
            [InlineKeyboardButton("↖️ Top-Left", callback_data="wm_set_pos_top-left"), InlineKeyboardButton("↗️ Top-Right", callback_data="wm_set_pos_top-right")],
            [InlineKeyboardButton("↙️ Bottom-Left", callback_data="wm_set_pos_bottom-left"), InlineKeyboardButton("↘️ Bottom-Right", callback_data="wm_set_pos_bottom-right")],
            [InlineKeyboardButton("🔙 Back", callback_data="set_watermark")]
        ]
        await message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("wm_set_pos_"):
        new_pos = data.replace("wm_set_pos_", "")
        if "watermark" not in settings: settings["watermark"] = {}
        settings["watermark"]["position"] = new_pos
        await update_user_settings(user_id, settings)

        callback_query.data = "set_watermark"
        await watermark_callback(client, callback_query)

    elif data == "wm_toggle_timing":
        modes = ["always", "range", "interval"]
        current = wm.get("timing_mode", "always")
        new_mode = modes[(modes.index(current) + 1) % len(modes)]

        if "watermark" not in settings: settings["watermark"] = {}
        settings["watermark"]["timing_mode"] = new_mode
        await update_user_settings(user_id, settings)

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
        await message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "wm_preview":
        await message.reply_text("⏳ Generating preview…")

        from bot.func.preview import generate_preview
        preview_path = await generate_preview(user_id, settings)

        if preview_path and os.path.exists(preview_path):
            await message.reply_photo(
                photo=preview_path,
                caption=f"{ICONS.watermark} <b>Watermark preview</b>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Delete", callback_data="cb_close")]])
            )
            os.remove(preview_path)
        else:
            await callback_query.answer("❌ Preview failed — check your watermark settings.", show_alert=True)


@Client.on_callback_query(filters.regex("^wm_edit_"))
async def wm_edit_callback(client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id
    message = callback_query.message
    log.info(f"WM Edit Callback: {data} for user {user_id}")

    settings = await get_user_settings(user_id)

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

    prompt_msg = await message.edit_text(text=prompt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cancel_input")]]))

    try:
        input_msg = await client.listen(chat_id=user_id, timeout=60)
        text = input_msg.text
        await input_msg.delete()
        log.info(f"WM Edit Input: {text}")
    except ListenerTimeout:
        await callback_query.answer("⏰ Timed out — try again.", show_alert=True)
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

    if "watermark" not in settings: settings["watermark"] = {}

    if data == "wm_edit_text":
        settings["watermark"]["text"] = text
    elif data == "wm_edit_size":
        if not text.isdigit() or not (10 <= int(text) <= 100):
            await callback_query.answer("❌ Size must be 10–100.", show_alert=True)
            return
        settings["watermark"]["font_size"] = text
    elif data == "wm_edit_opacity":
        try:
            val = float(text)
            if not (0.1 <= val <= 1.0): raise ValueError
        except ValueError:
            await callback_query.answer("❌ Opacity must be 0.1–1.0.", show_alert=True)
            return
        settings["watermark"]["opacity"] = text
    elif data == "wm_edit_border_opacity":
        try:
            val = float(text)
            if not (0.0 <= val <= 1.0): raise ValueError
        except ValueError:
            await callback_query.answer("❌ Border opacity must be 0.0–1.0.", show_alert=True)
            return
        settings["watermark"]["border_opacity"] = text
    elif data == "wm_edit_scale":
        try:
            val = float(text)
            if not (0.1 <= val <= 1.0): raise ValueError
        except ValueError:
            await callback_query.answer("❌ Scale must be 0.1–1.0.", show_alert=True)
            return
        settings["watermark"]["scale"] = text
    elif data.startswith("wm_edit_margin_"):
        side = data.split("_")[-1]
        if not text.isdigit():
            await callback_query.answer("❌ Enter a valid integer.", show_alert=True)
            return
        if "margins" not in settings["watermark"]: settings["watermark"]["margins"] = {}
        settings["watermark"]["margins"][side] = int(text)

        await update_user_settings(user_id, settings)
        await callback_query.answer(f"✅ {side.title()} margin updated", show_alert=False)

        # Return to margin menu
        callback_query.data = "wm_select_margins"
        await watermark_callback(client, callback_query)
        return

    await update_user_settings(user_id, settings)
    await callback_query.answer("✅ Saved", show_alert=False)

    # Return
    callback_query.data = "set_watermark"
    await watermark_callback(client, callback_query)


@Client.on_callback_query(filters.regex("^wm_upload_img"))
async def wm_upload_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    message = callback_query.message
    log.info(f"WM Upload Callback for user {user_id}")

    prompt_msg = await message.edit_text(
        text="<b>📤 Send your watermark image</b>\n<blockquote>PNG or JPG · max 5 MB</blockquote>\n<i>Press Cancel to abort.</i>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cancel_input")]]),
    )

    try:
        input_msg = await client.listen(chat_id=user_id, timeout=60)
        if not input_msg.photo and not input_msg.document:
            await callback_query.answer("❌ Not an image.", show_alert=True)
            return

        doc = input_msg.document or input_msg.photo
        if getattr(doc, "file_size", 0) and doc.file_size > 5 * 1024 * 1024:
            await callback_query.answer("❌ Image too large (max 5 MB).", show_alert=True)
            return

        # Download
        os.makedirs("watermarks", exist_ok=True)
        save_path = os.path.join(os.getcwd(), "watermarks", f"{user_id}.png")
        path = await input_msg.download(file_name=save_path)
        await input_msg.delete()

        settings = await get_user_settings(user_id)
        if "watermark" not in settings: settings["watermark"] = {}
        settings["watermark"]["image_path"] = path
        settings["watermark"].pop("image_data", None)  # never store blobs in Mongo
        saved = await update_user_settings(user_id, settings)

        if saved:
            await callback_query.answer("✅ Watermark image saved", show_alert=False)
        else:
            await callback_query.answer("⚠️ Could not save — try again.", show_alert=True)

    except ListenerTimeout:
        await callback_query.answer("⏰ Timed out — try again.", show_alert=True)
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
        try:
            await prompt_msg.delete()
        except Exception:
            pass
        await message.reply_text("❌ Failed to save the image. Please try again.")
        return

    # Return
    callback_query.data = "set_watermark"
    await watermark_callback(client, callback_query)


@Client.on_callback_query(filters.regex("^wm_config_timing"))
async def wm_timing_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    message = callback_query.message
    log.info(f"WM Timing Callback for user {user_id}")
    settings = await get_user_settings(user_id)
    mode = settings.get("watermark", {}).get("timing_mode", "always")

    prompt = ""
    if mode == "range":
        prompt = "<b>⏱ Range mode</b>\n<blockquote>Send <code>start end</code> in seconds, e.g. <code>10 60</code></blockquote>"
    elif mode == "interval":
        prompt = "<b>⏱ Interval mode</b>\n<blockquote>Send <code>duration period</code> — e.g. <code>5 30</code> shows for 5s every 30s.</blockquote>"
    else:
        await callback_query.answer("Mode is Always — no config needed.", show_alert=True)
        return

    prompt_msg = await message.edit_text(text=prompt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cancel_input")]]))

    try:
        input_msg = await client.listen(chat_id=user_id, timeout=60)
        text = input_msg.text
        await input_msg.delete()

        # Validate and Save
        parts = text.split()
        if len(parts) != 2:
            await callback_query.answer("❌ Format: two numbers (e.g. 10 60).", show_alert=True)
            return

        try:
            v1, v2 = float(parts[0]), float(parts[1])
        except ValueError:
            await callback_query.answer("❌ Numbers only.", show_alert=True)
            return

        if "watermark" not in settings: settings["watermark"] = {}

        if mode == "range":
            settings["watermark"]["start_time"] = v1
            settings["watermark"]["end_time"] = v2
        elif mode == "interval":
            settings["watermark"]["interval_duration"] = v1
            settings["watermark"]["interval_period"] = v2

        await update_user_settings(user_id, settings)
        await callback_query.answer("✅ Timing updated", show_alert=False)

    except ListenerTimeout:
        await callback_query.answer("⏰ Timed out — try again.", show_alert=True)
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


@Client.on_callback_query(filters.regex("^wm_upload_font"))
async def wm_upload_font_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    message = callback_query.message
    log.info(f"WM Font Upload Callback for user {user_id}")

    text = (
        "<b>🔤 Send your font file</b>\n"
        "<blockquote>TTF or OTF · max 5 MB</blockquote>\n"
        "<i>Press Cancel to abort.</i>"
    )
    await callback_query.answer()
    prompt_msg = await message.edit_text(
        text=text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cancel_input")]]),
    )

    try:
        input_msg = await client.listen(chat_id=user_id, timeout=60)
        if not input_msg.document or not input_msg.document.file_name.lower().endswith(('.ttf', '.otf')):
            await callback_query.answer("❌ Send a TTF/OTF font file.", show_alert=True)
            return

        if input_msg.document.file_size > 5 * 1024 * 1024:
            await callback_query.answer("❌ Font too large (max 5 MB).", show_alert=True)
            return

        # Ensure directory
        if not os.path.exists("watermarks/fonts"):
            os.makedirs("watermarks/fonts")

        # Download
        path = await input_msg.download(file_name=f"watermarks/fonts/{user_id}.ttf")
        await input_msg.delete()

        settings = await get_user_settings(user_id)
        if "watermark" not in settings: settings["watermark"] = {}
        settings["watermark"].pop("font_data", None)  # never store blobs in Mongo
        saved = await update_user_settings(user_id, settings)

        if saved:
            await callback_query.answer("✅ Custom font saved", show_alert=False)
        else:
            await callback_query.answer("⚠️ Could not save — try again.", show_alert=True)

    except ListenerTimeout:
        await callback_query.answer("⏰ Timed out — try again.", show_alert=True)
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
        try:
            await prompt_msg.delete()
        except Exception:
            pass
        await message.reply_text("❌ Failed to save the font. Please try again.")
        return

    # Return
    callback_query.data = "set_watermark"
    await watermark_callback(client, callback_query)


@Client.on_callback_query(filters.regex("^wm_timing_tutorial"))
async def wm_timing_tutorial_callback(client, callback_query: CallbackQuery):
    text = "<b>1. Range mode</b>\n<blockquote>Show only between two times.\n• Format: <code>start end</code>\n• Example: <code>10 60</code> → visible from 10s to 60s</blockquote>\n\n<b>2. Interval mode</b>\n<blockquote>Flash periodically.\n• Format: <code>duration period</code>\n• Example: <code>5 30</code> → on for 5s, every 30s\n• Windows: 0–5s, 30–35s, 60–65s…</blockquote>\n\n<b>3. Always</b>\n<blockquote>Watermark stays visible the whole time.</blockquote>"
    await callback_query.message.edit_text(
        text=text,
        reply_markup=InlineKeyboardMarkup([[back_btn("set_watermark")]])
    )


@Client.on_callback_query(filters.regex("^cancel_input"))
async def cancel_input_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    try:
        # Try stopping the listener
        await client.stop_listening(chat_id=user_id)
    except Exception as e:
        log.error(f"Error stopping listener for {user_id}: {e}")

    await callback_query.answer("🚫 Cancelled")


# Invoked via delegation from settings_callback (the ^set_ handler owns
# dispatch for all set_thumbnail* callback data).
async def thumbnail_callback(client, callback_query):
    user_id = callback_query.from_user.id
    settings = await get_user_settings(user_id)
    if not settings:
        settings = {}

    data = callback_query.data

    if data == "set_thumbnail":
        thumb_exists = "thumbnail" in settings
        status = "✅ Set" if thumb_exists else "❌ Not set"

        text = (
            f"{ICONS.thumb} <b>Custom thumbnail</b>\n\n"
            f"<blockquote>Status: {status}</blockquote>\n"
            f"Upload a custom thumbnail to embed in your finished videos."
        )

        buttons_list = [
            [InlineKeyboardButton("📤 Upload New", callback_data="set_thumbnail_upload")],
            [InlineKeyboardButton("🔙 Back", callback_data="set_main")]
        ]

        if thumb_exists:
            buttons_list.insert(0, [
                InlineKeyboardButton("👁️ View Current", callback_data="set_thumbnail_view"),
                InlineKeyboardButton("🗑️ Delete", callback_data="set_thumbnail_delete")
            ])

        buttons = InlineKeyboardMarkup(buttons_list)
        await callback_query.message.edit(text, reply_markup=buttons)

    elif data == "set_thumbnail_view":
        if "thumbnail" not in settings:
            await callback_query.answer("No thumbnail set.", show_alert=True)
            return

        # Save temp file to send
        import io
        thumb_data = settings["thumbnail"]
        f = io.BytesIO(thumb_data)
        f.name = "thumbnail.jpg"

        try:
            await client.send_photo(
                chat_id=user_id,
                photo=f,
                caption=f"{ICONS.thumb} <b>Your current thumbnail</b>"
            )
            await callback_query.answer()
        except Exception as e:
            log.error(f"Failed to send thumbnail: {e}")
            await callback_query.answer("Failed to send thumbnail.", show_alert=True)

    elif data == "set_thumbnail_delete":
        if "thumbnail" in settings:
            del settings["thumbnail"]
            await update_user_settings(user_id, settings)
            await callback_query.answer("🗑 Thumbnail deleted", show_alert=False)

            # Refresh menu
            # Better to just trigger base menu logic again
            callback_query.data = "set_thumbnail"
            await thumbnail_callback(client, callback_query)
        else:
            await callback_query.answer("No thumbnail to delete.", show_alert=True)

    elif data == "set_thumbnail_upload":
        await callback_query.message.edit(
            "<b>📤 Upload thumbnail</b>\n\n"
            "Send the photo you want embedded on finished videos.\n"
            "<i>Max 5 MB · press Cancel to abort.</i>",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_input")]]
            )
        )

        # Listen for photo
        try:
            input_msg = await client.listen(chat_id=user_id, filters=filters.photo, timeout=300)

            photo = input_msg.photo
            if getattr(photo, "file_size", 0) and photo.file_size > 5 * 1024 * 1024:
                await callback_query.answer("❌ Image too large (max 5 MB).", show_alert=True)
                return

            os.makedirs("thumbs", exist_ok=True)
            file_path = await client.download_media(input_msg, file_name=f"thumbs/{user_id}.jpg")

            if file_path and os.path.exists(file_path):
                # Keep the file on disk under THUMB_DIR; store only the path
                # (never raw image bytes in Mongo).
                settings["thumbnail"] = file_path
                settings.pop("thumbnail_data", None)
                await update_user_settings(user_id, settings)

                await input_msg.reply_text("<b>✅ Thumbnail saved!</b>")
                await callback_query.answer()

                # Return to menu
                callback_query.data = "set_thumbnail"
                await thumbnail_callback(client, callback_query)
            else:
                await input_msg.reply_text("❌ Failed to download the photo — please try again.")

        except ListenerTimeout:
            try:
                callback_query.data = "set_thumbnail"
                await thumbnail_callback(client, callback_query)
            except Exception:
                try:
                    await callback_query.message.edit("⏰ Timeout — open <code>/settings</code> to try again.")
                except Exception:
                    pass
        except Exception as e:
            if "ListenerCanceled" in str(e):
                 await safe_edit(callback_query.message, "🚫 Cancelled.")
            else:
                log.error(f"Thumbnail upload error: {e}")
                await safe_edit(callback_query.message, "❌ Something went wrong — try again.")


