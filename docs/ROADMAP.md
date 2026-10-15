# Roadmap

Continuous UI-first improvement rounds. One user-visible objective per round,
landing on a `round/NNN-<slug>` branch, fast-forwarded to `main`, tagged, then
re-verified by CI from the pushed commit. Roughly 40% of rounds ship a NEW
capability (section 6B), not just polish.

## Log

| Round | Objective | Change | Tier | Core-flow taps |
| --- | --- | --- | --- | --- |
| 1 | Settings callbacks | Exactly-once informative answering; Tier-0 UI snapshot + audit harness | 0 | unchanged (0) |
| 2 | Progress cards | Unified skeleton + pipeline stage chips for encode and transfer | 0 | unchanged (0) |
| 3 | Saved presets | Named presets (built-in + user), one-tap apply, `?start=preset_<key>` deep links | 0 | −2 for a repeat user (preset replaces ~3 taps) |

## Top candidate (next)

**Estimated output size & time before a job starts** (feature): after download,
show a "start encoding?" card with a projected size/time from the source
duration, bitrate and the chosen preset, so users can cancel *before* spending
minutes. Fold in the two quick-win fixes below if the round is light.

## Backlog (ranked)

1. Fix the `btn()` default-URL trap in `bot/utils/ui.py` and assert it in the UI
   audit (quick win).
2. Correct the `add_custom_callback` second-listener message id (quick win).
3. Extend the snapshot registry to the remaining top-level panels (start, queue).
4. Undo (grace window) for destructive actions (cancel / clear / reset).
5. Vertical 9:16 / aspect-ratio crop (needs a `scale/pad` model — blocks the
   promised "Reels 9:16" preset).
6. Audio-only / MP3 output (needs an audio-only extraction mode).
7. Contextual "?" help per card.
8. Tier-1 verification pass once a dedicated test bot + empty test DB exist.

## Attempted and rejected

- (none yet)
