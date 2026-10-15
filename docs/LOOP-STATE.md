# Loop state

Durable state across context compactions. Read this FIRST on resume.

- last_completed_round: 1 — settings callback feedback (v2.3.1)
- last_commit_sha: acfb254
- tag: v2.4.0 (round 2, this commit)
- current_round_number: 2
- current_objective: Unified progress-card skeleton + pipeline stage chips for
  encode and transfer (`bot/utils/progress_card.py`).
- todos_remaining: commit round 2, push `round/002-progress-card`, fast-forward
  `main`, tag `v2.4.0`, re-run CI from the pushed commit.
- next_candidate: Saved settings presets (section 6B.2) with `?start=preset_<key>`
  deep links — see docs/FEATURES.md.
- verify_tier: 0 (offline: unit tests + snapshots + UI audit). Not verified live.
- outstanding_blockers: none. `gh` CLI is unavailable locally, so GitHub Actions
  runs are not directly watchable; CI is re-run remote via `~/ci2.sh` from the
  pushed commit instead.

NEXT_ACTION = Commit round 2, push the branch, fast-forward main, tag v2.4.0,
then build the Saved Settings Presets feature as round 3.
