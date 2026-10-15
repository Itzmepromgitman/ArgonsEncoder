# Changelog

All notable changes to Argons Encoder are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the version is
exposed to users through `BOT_VERSION` (see the About panel).

## [2.4.0] - 2026-10-15

### Added
- **Pipeline stage chips on every progress card** (`Queued › Downloading ›
  Encoding › Delivering › Done`) so users can see where a job is, not just how
  far it has gone.
- `bot/utils/progress_card.py`: one shared progress skeleton (headline, stage
  chips, bar + percent, size, timing, metrics).
- Snapshot surfaces `encode_progress` and `transfer_progress`; the UI audit now
  covers three surfaces.

### Changed
- The encode card (`bot/func/encode.py::get_progress_ui`) and the transfer card
  (`bot/func/pyroutils/progress.py::progress_for_pyrogram`) now render through
  the same skeleton with the same `Value · Value · Value` line format. The
  transfer card no longer double-stamps an icon (title + percentage emoji).

### Fixed
- `progress_card.headline` clamps the displayed percentage to 0–100 so an
  out-of-range value can never print "150.0%".

## [2.3.1] - 2026-10-15

### Fixed
- **Settings feedback was silently dropped.** Telegram accepts exactly one
  answer per callback query. The settings UI answered an empty "stop the
  spinner" toast first and then tried to answer again with the real
  confirmation or error, which Telegram rejected as a duplicate and the code
  swallowed. Users therefore saw no confirmation when a setting was saved and
  no alert when it failed. The first informative answer now wins, and an empty
  answer is sent only when a handler has nothing informative to say. Implemented
  in `bot/utils/callback_ui.py` (`answer_once` + `guard_callback_answers`) and
  applied to every handler in `plugins/settings.py`.

### Added
- Tier-0 UI snapshot harness: `tests/ui_snapshots/` (golden surfaces),
  `tests/test_ui_snapshots.py` (drift detection, `UPDATE_SNAPSHOTS=1` to
  accept), and `scripts/ui_audit.py` (offline surface invariants: caption
  budget, callback-data length/duplication, actionability, navigation escape).
- CI now runs the UI audit between lint and unit tests.

## [2.3.0]

- Baseline release tracked by this changelog.
