# UI research

Findings from auditing the user-visible surfaces and from the mandatory research
loop (section 4). Append-only; newest entries at the bottom.

## Settings menu tree

`settings_callback` (`^set_|use_profile_|reset_`) owns dispatch for the whole
settings area:

- `set_video`, `set_meta` → `edit_callback` (`^edit_`): codec, preset, CRF,
  resolution, sample, trim, subtitles, metadata.
- `set_audio` → `edit_callback`: codec, bitrate, track.
- `set_watermark` → `watermark_callback` (`wm_*`): toggle, select, set, preview;
  plus `wm_edit_`, `wm_upload_img`, `wm_config_timing`, `wm_upload_font`,
  `wm_timing_tutorial`.
- `set_thumbnail` → `thumbnail_callback`: view, delete, upload.
- `set_more` → `toggle_simple_callback` (`toggle_video_remux`, `toggle_video_subs`,
  `toggle_output_mode`).
- `set_profiles` → `use_profile_` / `active_custom`; `set_custom` →
  `add_custom` / `del_custom` / `active_custom`.
- `reset_confirm` → `reset_`; `cancel_input` → `cancel_input_callback`.

## Ranked defects

1. **Dropped settings feedback (fixed, round 1).** Every settings entry answered
   an empty toast and then tried to answer again; Telegram rejects the second.
   See `bot/utils/callback_ui.py`.
2. **`btn()` default `url`.** `bot/utils/ui.py` defaults `btn(...)` to
   `url="https://t.me/"`; a call that forgets `callback_data` silently builds a
   link. The UI audit now catches non-actionable cells at build time.
3. **Custom-FFmpeg second listener.** `add_custom_callback` registers its second
   `listen_once` against the panel message id rather than the prompt message id.
4. **Two progress renderers (fixed, round 2).** `get_progress_ui` and
   `progress_for_pyrogram` rendered differently; both now build the shared
   skeleton in `bot/utils/progress_card.py` and show pipeline stage chips.
5. **Inconsistent navigation.** The audit enforces a Home/Back/Close/Cancel
   escape per surface, so new panels cannot drift.

## 2026-10-15 — Round 2 research (Telegram platform sweep)

Sources: `https://core.telegram.org/bots/api-changelog` (Bot API 10.0–10.3,
May–Aug 2026) and `https://telecrow.com/guides/telegram-bot-api-2026-changes`
(2026-09-02).

MTProto/Pyrofork-reachable deltas (adoptable):

- **`?start=<payload>` deep-linking.** `t.me/<bot>?start=foo` delivers
  `/start foo` to the bot's own command handler — reachable from a userbot and
  ideal for shareable settings presets. Adopted in the presets feature.
- **Expandable blockquotes** (`MessageEntityBlockquote` with `collapsed=True`).
  Reachable via MTProto HTML `<blockquote expandable>`; candidate for long
  help/research cards. Verify in the installed Pyrofork before use.

Bot-API-only (NOT usable from a Pyrofork userbot — recorded so we stop
re-deriving it):

- Disabled buttons (`DisabledButton` / `InlineKeyboardButton.disabled`, 10.3) —
  the MTProto `keyboardButton` has no disabled flag. Keep the existing pattern:
  keep the button, make a tap answer with a reason.
- Ephemeral messages, Guest Mode, Rich Messages, Checklists — Bot-API surfaces
  only. Not reachable from MTProto.

## 2026-10-15 — Round 3 research (presets & deep linking)

- **Deep-link payload path is already wired.** `plugins/start.py::start` reads
  `message.command[1]` into `payload` and branches on `settings`/`queue`. Round 3
  extends the same branch with `preset_<key>` → `apply_named_preset`, so no new
  command or handler registration is needed. Keys must be lower-case (the
  payload is lower-cased before the branch).
- **The audit's `nav` rule forces a way out.** `presets_menu` passes because it
  ends in `back_btn("set_main")`; the delete button (🗑️) must therefore never
  replace the Back row — it is added to the preset's own row.
- **Naming collision found.** The settings menu already had a "Presets" button
  that only changes encoder quality (`QUALITY_PRESETS`: fast/balanced/compact).
  Two screens both called "Presets" would confuse users, so the quality one is
  relabelled **🎚 Quality** and the new screen keeps the "Presets" name for the
  thing users mean by a preset (resolution + codec + delivery).
- **Model limit discovered.** `resolution` is height-only and audio codecs are
  `aac/ac3/copy`, so the "Reels 9:16" and "Audio-only MP3" preset ideas from the
  brief are **not** implementable without new FFmpeg plumbing. Shipped honest
  presets instead and logged the two gaps in the roadmap.

## Verification tier

Rounds 1–3 verified at **Tier 0** (unit tests, snapshots, UI audit). Not verified
live; live checks are a Tier-1 follow-up once a test bot + empty test DB exist.
