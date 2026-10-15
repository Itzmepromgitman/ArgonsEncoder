# Loop state

Durable state across context compactions. Read this FIRST on resume.

- last_completed_round: 3 — saved settings presets + deep links (v2.5.0, CI-fixed v2.5.1)
- last_commit_sha: 04853c6
- tag: v2.5.1 (round 3 + snapshot-golden fix)
- current_round_number: 3
- current_objective: Saved settings presets — built-in + user-saved overlays,
  one-tap apply, `?start=preset_<key>` deep links
  (`bot/utils/presets.py`, `bot/utils/settings.py`, `plugins/settings.py`,
  `plugins/start.py`).
- todos_remaining: commit round 3, push `round/003-presets`, fast-forward `main`,
  tag `v2.5.0`, re-run CI from the pushed commit.
- next_candidate: estimated output size & time before a job starts (see
  docs/ROADMAP.md); quick wins available (btn() default-url trap,
  add_custom second-listener id).
- verify_tier: 0 (offline: unit tests + snapshots + UI audit). Not verified live.
- outstanding_blockers: none. `gh` CLI unavailable locally; CI re-run remote via
  `~/ci2.sh` from the pushed commit.
- process note: when regenerating snapshots on the remote, copy back **every**
  changed golden, not just the new surface — otherwise a stale golden ships and
  CI goes red on the pushed tag (this is what v2.5.1 fixed).

NEXT_ACTION = Commit the 2.5.1 snapshot fix on round/003-presets, push, fast-
forward main, tag v2.5.1, re-run CI from the pushed commit, then build the
"estimated size & time before encode" feature as round 4.
