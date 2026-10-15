# Loop state

Durable state across context compactions. Read this FIRST on resume.

- last_completed_round: 2 — unified progress cards + stage chips (v2.4.0)
- last_commit_sha: 5f7a79b
- tag: v2.5.0 (round 3, this commit)
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

NEXT_ACTION = Commit round 3, push the branch, fast-forward main, tag v2.5.0,
then build the "estimated size & time before encode" feature as round 4.
