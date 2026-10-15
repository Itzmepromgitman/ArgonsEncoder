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

## 2026-10-15 — Round 4 research (estimate card before encode)

Sources (all fetched 2026-10-15): `https://core.telegram.org/bots/api-changelog`
(Bot API 10.0 2026-05-08 … 10.3 2026-08-24), `https://core.telegram.org/bots/features`,
`https://core.telegram.org/api/entities`, `https://core.telegram.org/api/effects`,
`https://core.telegram.org/constructor/message`. Competitor UX:
`tinyfile.bot/video-compressor`, `flowmark-app.com/en/compress-video-for-telegram`,
`github.com/The-Hunter-Oraganization/Erika-Amano`.

### Estimate-card layout (reuse `progress_card.py` skeleton)

The card is a *pre-encode twin* of the progress card: same headline + chips, but
the blockquote holds three estimate rows instead of a progress bar, and the
keyboard adds a **Start** action. Proposed shape:

```
🎬 Ready to encode
`video.mp4` · 24.3 MB · 1080p · 2:14

> 📦 Output  ~85–120 MB   (Auto)
> ⏱ Time    ~2–4 min  (H.264 · CRF 23 · medium)
> ⚙️ 1080p · AAC 128k · 2 audio channels

[ ✂️ Trim ] [ 🎯 Target size ]
[ ⚙️ Settings ] [ ▶️ Start encode ]
```

Layout rules:

- **Ranges, never single numbers.** Emit `~low–high` (see the bounds in
  `FEATURES.md`). A single "≈104 MB" over-promises and erodes trust on the first
  miss.
- **Numbers go in the blockquote**, so the eye groups the three rows; **params go
  in `code`/`pre`** (already used elsewhere) so they scan as machine values.
- Keep the card to **one screen**: 3 rows max in the blockquote. If a 4th metric
  is needed, drop it into the headline chips, not the quote.
- The estimate replaces the bare "send a video" prompt only *after* the probe
  parse succeeds; a failed probe shows the card without the estimate rows (never
  a fabricated number).
- **Start is the only action that fires the encode.** Everything else edits the
  card. Do not encode on intake.

### Interaction findings (what to adopt from the sweep)

- **Confirm-before-encode is the whole point.** The intake message is already an
  inline keyboard; add `▶️ Start encode` → `callback_data` that enqueues. Until
  it is pressed, nothing runs. This is the single highest-value delta of the round.
- **Edit, don't re-send.** Estimate → progress → done should be edits of the same
  message (the repo already edits panels). Only the final media is a new message
  (media cannot be attached by edit). Matches the Feature-guide advice to prefer
  `editMessageReplyMarkup`/edit over send+delete.
- **Re-render on settings change.** Changing CRF/preset/resolution from the card's
  `⚙️ Settings` must recompute and re-edit the estimate rows in place, so the user
  sees the cost of each knob before paying it.
- **Target-size mode.** Competitors lead with it and it is cheap for us: a
  `🎯 Target size` button offers 10 / 16 / 25 MB + custom (TinyFile's exact set),
  and switches the `📦 Output` row to "will target 16 MB" while flagging that a
  targeted encode is slower.

### Competitor UX notes

- **TinyFile** — "shows an estimated time up front and live progress"; keyboard
  targets `🎯 Auto / 10 MB Discord / 16 MB WhatsApp / 25 MB Email / ✏️ Custom`;
  choice is remembered; "already fits → says so, charges nothing".
- **Flowmark** — `Compress to X MB` (1–200 MB); "you set the ceiling, the bot
  works out the parameters"; if the file already fits it is left untouched.
- **Erika-Amano** (open-source encoder bot) — after-encode "smart caption" with a
  thumbnail and a **before/after file-size comparison**. We can mirror this by
  editing the estimate card into a result card that shows *estimated vs actual*.

### Telegram formatting levers for this card

All rows below are MTProto-reachable unless stated; dates from the changelog.

- **Blockquote** (`messageEntityBlockquote`) for the three estimate rows —
  `api/entities`, 2026-10-15.
- **Expandable blockquote** (`<blockquote expandable>`) to collapse the params
  behind the headline so the card stays one screen — CANDIDATE, verify the
  installed Pyrofork HTML parser supports `expandable` before relying on it.
- **Formatted-date entity** (`messageEntityFormattedDate`, Bot API 9.5,
  2026-03-01) — render the estimated finish time as a tappable/relative date.
- **Message effect** (`effect` field, flags2.2) on the *finish* message only —
  reachable via `messages.getAvailableEffects`; use sparingly, not on every job.
