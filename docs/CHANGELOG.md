# Changelog

All notable changes to Argons Encoder are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the version is
exposed to users through `BOT_VERSION` (see the About panel).

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
