# Architecture

Argons Encoder is a Telegram bot (Pyrofork / MTProto) that downloads a video,
runs it through `ffmpeg`, and streams the result back as either a video or a
document.

## Layout

| Path | Responsibility |
| --- | --- |
| `bot/config.py` | Environment-backed configuration, defaults, `validate_config`, `BOT_VERSION`. |
| `bot/__main__.py` | Process entry point: client bootstrap, plugin loading, shutdown. |
| `bot/utils/ui.py` | Shared design system: `ICONS`, `btn`, `back_btn`, `close_btn`, `card`, `error_card`, `empty_state`, `safe_edit`, `progress_bar`, `truncate`, `state_label`. |
| `bot/utils/callback_ui.py` | Exactly-once callback answering (`answer_once`, `guard_callback_answers`). |
| `bot/utils/progress_card.py` | Shared progress skeleton: headline, pipeline stage chips, bar + percent, size, timing. |
| `bot/utils/presets.py` | Named encoding presets: built-in + user-saved overlays, sanitation, merge. |
| `bot/utils/settings.py` | `normalize_settings` and range/default validation for user settings; `apply_settings_preset`. |
| `bot/utils/format.py` | `humanbytes`, `format_time`. |
| `bot/utils/listener.py` | `listen_once` / `cancel_session` input prompts with a busy guard. |
| `bot/func/encode.py` | Encoding pipeline, progress UI, upload + retry machinery. |
| `bot/func/queue_manager.py` | Download/encode/upload job queue and worker slots. |
| `plugins/` | Handlers: `start`, `encode`, `query`, `queue`, `settings`, `admin`, `screenshot`. |
| `database.py` | Motor/MongoDB access, settings cache, tombstones, stats. |
| `scripts/ui_audit.py` | Offline UI surface invariant checks (Tier 0). |
| `tests/` | Offline unit tests + UI snapshots. |

## Callback answering model

Pyrofork maps `CallbackQuery.answer` to a single `messages.SetBotCallbackAnswer`
call per query id. A second answer for the same query is rejected by Telegram.

Settings callbacks are nested (a handler such as `settings_callback` re-dispatches
to `watermark_callback` or `thumbnail_callback` and then re-renders itself), so
naively answering "stop the spinner" first would consume the one allowed answer
and drop any confirmation that followed.

`bot/utils/callback_ui.py` resolves this:

- `answer_once(query, text, show_alert)` — the first *informative* call (text or
  alert) is sent immediately; an empty call is deferred while a guarded handler
  is running.
- `guard_callback_answers` — a decorator that tracks nesting depth per query id
  and flushes a deferred empty answer at the outermost exit. State is removed in
  a `finally`, so exceptions cannot leak memory or leave a spinner running.

Handlers in `plugins/settings.py` keep their existing `_safe_callback_answer`
helper; it now delegates to `answer_once`, so call sites are unchanged.

## Progress model

Every progress surface (encode, upload, download) renders through
`bot/utils/progress_card.py` so the three read as one product:

- `stage_chips(stage)` — the pipeline `Queued › Downloading › Encoding ›
  Delivering › Done`, with done/active/pending markers.
- `headline`, `size_line`, `timing_line`, `progress_block` — the shared line
  grammar.
- `card(...)` — assembles headline, chips, optional subtitle, the blockquote
  block, and optional footer blocks.

`FFmpegProcess.get_progress_ui` (`bot/func/encode.py`) and
`progress_for_pyrogram` (`bot/func/pyroutils/progress.py`) both call `card`.

## Preset model

A preset is a bounded **overlay** over encoding knobs only (resolution, video
codec / CRF / encoder preset, remux / subtitle mode, audio codec / bitrate /
track, `output_as_video`). It never carries trim, metadata, rename, watermark or
thumbnail values, so applying one cannot silently change branding.

- `bot/utils/presets.py` owns the built-ins, `sanitize_overlay` (shape only),
  `sanitize_user_presets` (≤12, validated names, `u_` keys), `extract_overlay`,
  `overlay_summary`, and `merged_presets`.
- `bot/utils/settings.py` owns persistence: `presets` lives in the settings
  document and `apply_settings_preset` re-normalizes the result, so range
  enforcement (`codec`, `crf`, `bitrate`, `resolution`) always applies after a
  merge. Sanitation shapes; normalization enforces.
- Keys are stable lower-case slugs, which lets a preset travel in a Telegram
  deep link (`?start=preset_web_1080p`, routed by `plugins/start.py`).

## Testing tiers

- **Tier 0** — offline, zero secrets, no network: `compileall`, ruff, the UI
  audit, `verify_ffmpeg_cmd.py`, and `pytest tests/`. Must be green on every
  commit; this is what CI enforces.
- **Tier 1** — live Telegram verification. Requires a dedicated test bot and an
  empty test database; never run against production credentials.
