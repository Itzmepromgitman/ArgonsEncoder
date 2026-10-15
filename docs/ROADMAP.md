# Roadmap

Continuous UI-first improvement rounds. One user-visible surface per round, each
landing on a `round/NNN-<slug>` branch, fast-forwarded to `main`, tagged, and
re-verified by CI from the pushed commit.

## Log

| Round | Surface | Change | Tier |
| --- | --- | --- | --- |
| 1 | Settings callbacks | Exactly-once informative answering; Tier-0 UI snapshot + audit harness | 0 |

## Backlog (ranked)

1. Unify the two progress renderers (`bot/func/encode.py::get_progress_ui` and
   `bot/func/pyroutils/progress.py`) behind the shared design system.
2. Fix the `btn()` default-URL trap in `bot/utils/ui.py` and assert it in the UI
   audit.
3. Correct the `add_custom_callback` second-listener message id.
4. Extend the snapshot registry beyond `settings_menu` to the other top-level
   panels (start, queue, encode progress).
5. Tier-1 verification pass for the round-1 settings feedback once a dedicated
   test bot and empty test database are available.
