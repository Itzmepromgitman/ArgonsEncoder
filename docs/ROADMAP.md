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
| 3 | Saved presets | Named presets (built-in + user), one-tap apply, `?start=preset_<key>` deep links | 0 | −2 for a repeat user |
| 4 | Size/time estimate | Pure estimate model rendered on the encode card before bytes appear | 0 | unchanged (0) |

## Top candidate (next)

**Confirm-before-encode intake card** (feature): reuse the round-4 estimate to
show a "▶️ Start / ✖ Cancel" card for *long* jobs only, so the common short path
keeps its tap count while a multi-hour encode gets a deliberate gate. Also
surface the estimate on the intake/"downloading" card where the Telegram-supplied
duration is already known.

## Backlog (ranked)

1. Formatted-date entity (`date_time`) for a tappable/relative ETA.
2. Message effect on completion (`messages.getAvailableEffects`) + reaction on
   intake — cheap delight, MTProto-reachable.
3. Undo (grace window) for destructive actions (cancel / clear / reset).
4. Quick win: fix the `btn()` default-URL trap in `bot/utils/ui.py` + audit assert.
5. Quick win: correct the `add_custom_callback` second-listener message id.
6. Extend the snapshot registry to the remaining top-level panels (start, queue).
7. Vertical 9:16 / aspect-ratio crop (needs a `scale/pad` model).
8. Audio-only / MP3 output (needs an audio-only extraction mode).
9. Contextual "?" help per card.
10. Tier-1 verification pass once a dedicated test bot + empty test DB exist.

## Attempted and rejected

- (none yet)
