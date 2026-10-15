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

## Top candidate (next)

**Saved settings presets** (feature, section 6B.2 — the highest-value item):
named full-profile presets (built-in + user-saved) applied in one tap, with
`?start=preset_<key>` deep links to share one. See `docs/FEATURES.md`.

## Backlog (ranked)

1. Fix the `btn()` default-URL trap in `bot/utils/ui.py` and assert it in the UI
   audit.
2. Correct the `add_custom_callback` second-listener message id.
3. Extend the snapshot registry to the remaining top-level panels (start, queue).
4. Estimated output size + encode time shown BEFORE a job starts.
5. Undo (grace window) for destructive actions (cancel / clear / reset).
6. Contextual "?" help per card.
7. Tier-1 verification pass once a dedicated test bot + empty test DB exist.

## Attempted and rejected

- (none yet)
