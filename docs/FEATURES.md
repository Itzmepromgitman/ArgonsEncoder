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
