# Features

Capability audit (6B.1) and shipped feature specs (6B.2). Append-only.

## 6B.1 Telegram capability audit — 2026-10-15

Checked against `core.telegram.org/bots/api-changelog` (Bot API 10.0–10.3,
May–Aug 2026) and the pinned Pyrofork (MTProto). Status values: ADOPTED,
CANDIDATE, REJECTED (not reachable from MTProto).

| Capability | Problem it solves | Reachable? | Effort | Value | Status |
| --- | --- | --- | --- | --- | --- |
| `?start=<payload>` deep link | Share a preset in one link | Yes (MTProto) | S | 5 | ADOPTED (Round 3, v2.5.0) |
| Expandable blockquote entity | Long cards without walls of text | Yes (MTProto) | S | 3 | CANDIDATE |
| Message reaction on intake | Ack without a text reply | Yes (MTProto) | S | 2 | CANDIDATE |
| Custom emoji entities | Branded look | Yes (MTProto) | M | 2 | CANDIDATE |
| Disabled inline buttons | No layout shift | No (Bot API) | — | — | REJECTED |
| Ephemeral messages | Group-only transients | No (Bot API) | — | — | REJECTED |
| Guest Mode | Act in non-member chats | No (Bot API) | — | — | REJECTED |
| Rich Messages | Tables/structured cards | No (Bot API) | — | — | REJECTED |
| Checklist / task lists | Batch-pipeline card | No (Bot API) | — | — | REJECTED |

## Shipped features

### Unified progress card (Round 2, v2.4.0)

- **Problem:** download, upload and encode each rendered progress differently, so
  the product felt like three bots.
- **Design:** one skeleton in `bot/utils/progress_card.py` — headline, pipeline
  stage chips (Queued › Downloading › Encoding › Delivering › Done), then a
  blockquote with bar + percent, size, timing (speed · elapsed · ETA) and extra
  metrics. Both `FFmpegProcess.get_progress_ui` and `progress_for_pyrogram`
  build through it.
- **Screens:** the encode card and the transfer card.
- **DB / config:** none.
- **Failure modes:** unknown stage → chips omitted (no crash).
- **Tested:** `tests/test_progress_card.py`, snapshot surfaces `encode_progress`
  and `transfer_progress`.

### Saved settings presets (Round 3, v2.5.0)

- **Problem:** users re-tap the same encoding settings on every video, and there
  is no way to share a known-good profile with someone else.
- **Design:** a *preset* is a bounded **overlay** over the encoding knobs only —
  resolution, video codec, CRF, encoder preset, remux / subtitle mode, audio
  codec / bitrate / track, and delivery format (`output_as_video`). Applying a
  preset therefore **never** touches trim, metadata, rename pattern, watermark or
  thumbnails, so a one-tap preset can never delete a user's branding.
  - `bot/utils/presets.py`: four built-ins (`🌐 Web 1080p`, `📱 Social 720p`,
    `📦 Compact 480p`, `🪶 Tiny 360p`), `sanitize_overlay`,
    `sanitize_user_presets` (≤12 saved, name `1–24` chars, `[A-Za-z0-9 _-]`),
    `extract_overlay`, `overlay_summary`, `merged_presets`.
  - Keys are stable lower-case slugs (`u_` prefix for user presets) so a preset
    can ride in a Telegram deep link: `?start=preset_web_1080p`.
  - `bot/utils/settings.py`: persists `presets` in the settings document and
    exposes `apply_settings_preset` (re-normalizes, so a malformed overlay can
    never write an out-of-range codec / CRF / bitrate).
- **Screens:** `plugins/settings.py::presets_menu_card` — one button per preset
  (user presets get a 🗑️ delete), a "💾 Save current as preset" action that
  prompts for a name via the shared listener, and Back to settings. The main
  menu's old "Presets" button is relabelled **🎚 Quality** (it only changes
  encoder settings) to disambiguate it from the new screen.
- **Deep link:** `plugins/start.py` routes `?start=preset_<key>` to
  `apply_named_preset`, which confirms with the preset's label and summary.
- **DB / config:** one new settings key (`presets`), normalized on every read.
- **Failure modes:** unknown key → "that preset no longer exists"; save limit →
  alert; oversized/garbage stored value → dropped by the normalizer.
- **Not covered (honest gaps):** vertical `9:16` framing is **not** supported —
  `resolution` is height-only (`1080p/720p/480p/360p`), so `📱 Social 720p`
  fixes height, not aspect ratio. Audio-only / MP3 output is **not** supported —
  audio codecs are `aac/ac3/copy` and there is no `-vn` mode. Both are recorded
  in the roadmap backlog rather than faked.
- **Tested:** `tests/test_presets.py`, snapshot surface `presets_menu`.

## Planned features

- None currently specced. See `docs/ROADMAP.md` for the ranked backlog.

## 6B.1b Capability re-audit — 2026-10-15 (Round 4)

Re-checked against `core.telegram.org/bots/api-changelog` (10.0 2026-05-08,
10.1 2026-06-11, 10.2 2026-07-14, 10.3 2026-08-24), `core.telegram.org/api/entities`,
`core.telegram.org/api/effects` and `core.telegram.org/constructor/message`
(fetched 2026-10-15). "Reachable" = usable from the project's own MTProto/Pyrofork
client; Bot-API-only items are listed so nobody spends a round on them again.

| Capability | Value here | Reachable from MTProto/Pyrofork? | Effort | Status |
| --- | --- | --- | --- | --- |
| Confirm-before-encode Start button | Gate cost behind one tap | Yes (inline keyboard) | S | CANDIDATE (this round) |
| Edit-in-place estimate→progress→done | One card, no chat spam | Yes (`editMessageText`) | S | CANDIDATE (this round) |
| Formatted-date entity (`date_time`, 9.5) | Tappable ETA / finish time | Yes (`messageEntityFormattedDate`) | S | CANDIDATE |
| Blockquote for estimate rows | Group the numbers | Yes (`messageEntityBlockquote`) | XS | ADOPT (formatting) |
| Expandable blockquote | Collapse params | Yes* (verify Pyrofork parser) | S | CANDIDATE |
| Message effect on finish (`effect:long`) | Celebrate completion | Yes (`messages.getAvailableEffects`) | S | CANDIDATE |
| Reactions on intake | Ack without text | Yes (`messages.sendReaction`) | XS | CANDIDATE |
| Custom-emoji entities | Branded look | Yes (premium account) | M | CANDIDATE |
| `?start=<payload>` deep link | Share presets | Yes | S | ADOPTED (R3) |
| Native video-quality / alt_document | Warn "still processing" | Yes (read on received msg) | M | REJECTED (low value) |
| Disabled inline button (`disabled`, 10.3) | Grey out Start | **No (Bot API)** | — | REJECTED |
| Button colour `style` (9.4) / `icon_custom_emoji_id` | Accent buttons | **No (Bot API)** | — | REJECTED |
| `force_reply` on inline keyboards (10.3) | Force a reply | **No (Bot API)** | — | REJECTED |
| Ephemeral messages (10.2/10.3) | Private group replies | **No (Bot API)** | — | REJECTED |
| Rich Messages / tables / details (10.1/10.2) | Structured cards | **No (Bot API)** | — | REJECTED |
| Streaming drafts `sendMessageDraft` (9.3/9.5) | Stream text output | **No (Bot API)** | — | REJECTED |
| Guest Mode (10.0) / Communities (10.2) | Non-member replies | **No (Bot API)** | — | REJECTED |
| Checklists (9.1) / Live Photos (10.0) | Batch/album cards | **No (Bot API)** | — | REJECTED |

\* `expandable` blockquote: the MTProto entity exists; confirm the pinned Pyrofork
build parses `<blockquote expandable>` before shipping, otherwise use a plain
blockquote + a "details" caption.

### NOT supported from Pyrofork/MTProto — stop re-deriving

- **Disabled buttons** (`DisabledButton`, Bot API 10.3, 2026-08-24): the MTProto
  `keyboardButton` has no disabled flag. Pattern stays: keep the button, answer a
  tap with a reason (`callback.answer("finish the current job first")`).
- **Button colours** (`style`, 9.4) and custom-emoji button icons: Bot-API reply
  markup only; MTProto renders buttons without the field.
- **Ephemeral messages, Guest Mode, Communities, Rich Messages, Checklists,
  Live Photos, streaming drafts**: all Bot-API surfaces. Not reachable from a
  Pyrofork userbot/bot client.
- **`force_reply` on inline keyboards** (10.3): Bot-API only; the existing
  `listen_once` prompt-listener pattern remains the way to force the next input.

## Shipped feature — Estimated output size & time (Round 4, v2.6.0)

Status: SHIPPED (Round 4). No new taps.

- **Problem:** a job starts encoding with no warning of the output size or how
  long it will take, so users cannot choose a safer preset or trim *before*
  paying CPU time.
- **Design:** a pure model in `bot/utils/estimate.py` computes a size and time
  *range* from the probed duration + the resolved video/audio settings, and the
  encode card renders it as **one** extra line before any bytes appear:
  `🎯 ≈ 38–57 MB · ⏱ ≈ 2–4 min`. It rides the existing progress card
  (`progress_card.card(..., extra_lines=...)`), so there is no new button and no
  new callback.
- **Estimate model (bounded, table-driven):**
  - `bpp = 0.12 × 0.87^(crf − 23) × codec_factor` (x264 reference; +6 CRF ≈ −50 %
    size). `codec_factor`: x264 1.0, x265 0.55, mpeg4 1.6, vp9/av1 0.44.
  - `size_bits = bpp × pixel_factor × 1920×1080 × 30 fps × duration`
    `+ audio_kbps × 1000 × duration`. `pixel_factor`: 1080p 1.0, 720p 0.44,
    480p 0.2, 360p 0.11.
  - `seconds = duration × codec_factor / (preset_factor × resolution_factor)`,
    `preset_factor` relative to medium (ultrafast 6.0 … veryslow 0.25),
    `codec_factor` relative to x264 (x265 2.5, vp9 6.0, av1 12.0),
    `resolution_factor`: 1080p 1.0, 720p 0.5, 480p 0.24, 360p 0.14.
  - Size widened to `0.7×–1.4×`, time to `0.5×–2.0×`; size clamped to
    `[0.04×, 3.0×]` source and floored at 40 KB; time clamped to `[5 s, 6 h]`.
- **Screens:** the encode progress card (`FFmpegProcess.get_progress_ui`), on the
  first render. Actual size replaces nothing — the runtime `🗜 smaller/larger`
  line is untouched.
- **DB / config:** none. Pure function of probe + settings.
- **Failure modes:** unknown codec/preset/resolution → documented defaults; bad
  CRF → 23; zero/absent duration → no estimate line (never a guess). The
  estimators are total and never raise.
- **Not covered:** an intake card with a confirm-before-encode `▶️ Start` button
  (adds a tap — out of scope this round) and exact 2-pass size targeting.
- **Tested:** `tests/test_estimate.py` (monotonicity, clamps, total on garbage),
  snapshot surface `encode_progress` now includes the estimate line.
