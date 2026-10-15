# UI research

Findings from auditing the user-visible surfaces, kept here so later rounds can
pick up the highest-impact item without re-deriving it.

## Settings menu tree

`settings_callback` (`^set_|use_profile_|reset_`) owns dispatch for the whole
settings area:

- `set_video`, `set_meta` → `edit_callback` (`^edit_`): codec, preset, CRF,
  resolution, sample, trim, subtitles, metadata.
- `set_audio` → `edit_callback`: codec, bitrate, track.
- `set_watermark` → `watermark_callback` (`wm_*`): toggle, select, set, preview;
  plus `wm_edit_`, `wm_upload_img`, `wm_config_timing`, `wm_upload_font`,
  `wm_timing_tutorial`.
- `set_thumbnail` → `thumbnail_callback`: view, delete, upload (replaces the
  panel with a live listener prompt).
- `set_more` → `toggle_simple_callback` (`toggle_video_remux`, `toggle_video_subs`,
  `toggle_output_mode`).
- `set_profiles` → `use_profile_` / `active_custom`; `set_custom` →
  `add_custom` / `del_custom` / `active_custom`.
- `reset_confirm` → `reset_`; `cancel_input` → `cancel_input_callback`.

## Ranked defects

1. **Dropped settings feedback (fixed in round 1).** Every settings entry
   answered an empty "stop the spinner" toast and then answered again with the
   real confirmation or error. Telegram rejects the second answer, so users saw
   no feedback. See `docs/ARCHITECTURE.md` and `bot/utils/callback_ui.py`.
2. **`btn()` default `url`.** `bot/utils/ui.py` defaults `btn(...)` to
   `url="https://t.me/"`, so any call that forgets `callback_data` silently
   builds a link button instead of an actionable one. The UI audit now catches
   the resulting non-actionable/invalid cases at build time.
3. **Custom-FFmpeg second listener.** `add_custom_callback` registers its second
   `listen_once` against the panel message id rather than the freshly created
   prompt message id, so the reply can be matched to the wrong message.
4. **Two progress renderers.** `bot/func/encode.py::get_progress_ui` and
   `bot/func/pyroutils/progress.py` render transfer/encode progress differently;
   converging them is the main UI-consistency win.
5. **Inconsistent navigation.** Not every leaf panel exposes a Back; the audit
   now enforces a Home/Back/Close escape per surface, so new panels cannot drift.

## Verification tier

Round 1 was verified at **Tier 0** (offline: unit tests, snapshots, UI audit).
It was **not** verified live against Telegram; live confirmation of the settings
toasts is a Tier-1 follow-up once a test bot and empty test database exist.
