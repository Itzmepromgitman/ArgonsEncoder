# Features

Capability audit (6B.1) and shipped feature specs (6B.2). Append-only.

## 6B.1 Telegram capability audit — 2026-10-15

Checked against `core.telegram.org/bots/api-changelog` (Bot API 10.0–10.3,
May–Aug 2026) and the pinned Pyrofork (MTProto). Status values: ADOPTED,
CANDIDATE, REJECTED (not reachable from MTProto).

| Capability | Problem it solves | Reachable? | Effort | Value | Status |
| --- | --- | --- | --- | --- | --- |
| `?start=<payload>` deep link | Share a preset in one link | Yes (MTProto) | S | 5 | ADOPTED (Round 3) |
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

## Planned features

### Saved settings presets (Round 3, target v2.5.0)

- **Problem:** users re-tap the same 6 settings every time; there is no
  one-tap "Reels 9:16" / "Audio-only MP3" path.
- **Design:** named full-profile presets (built-in + user-saved), one tap to
  apply, plus `?start=preset_<key>` deep links to share one. Spec finalised at
  the top of the Round 3 entry.
