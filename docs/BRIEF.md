# BRIEF — Round 4 (scratchpad, rewritten each round)

OBJECTIVE: Surface an estimated output size and encode time on a job's early
card, before the encode completes, from the source duration + resolved settings.

WHY NOW: competitors (TinyFile, Flowmark) show time/size up front; today the bot
shows nothing until bytes appear. `EncodingStats.estimated_size` already exists
(bot/func/encode.py:700) but is never populated.

FILES:
- bot/utils/estimate.py (NEW, pure, no I/O, no client)
- bot/func/encode.py (populate the estimate at :1519; render in get_progress_ui ~:989)
- tests/test_estimate.py (NEW), tests/ui_snapshot_utils.py + tests/ui_snapshots/encode_progress.json
- docs/FEATURES.md, docs/CHANGELOG.md, docs/ARCHITECTURE.md, README.md, plugins/start.py (/features,/help), bot/config.py (BOT_VERSION 2.6.0)

EXACT API (both workers code to this; do not invent extra symbols):
  estimate_output_bytes(duration_s, resolution, codec, crf, *, audio_kbps=128, source_bytes=0) -> tuple[int,int]
  estimate_encode_seconds(duration_s, resolution, codec, preset) -> tuple[int,int]
  format_estimate(size_range, secs_range) -> str   # e.g. "🎯 ≈ 38–57 MB · ⏱ ≈ 2–4 min"
  Tables: bpp(crf,codec) with bpp_x265=0.55*bpp_x264; preset speed+size factors;
  resolution pixel factors for 1080p/720p/480p/360p. Pure + deterministic.

ACCEPTANCE (testable):
1. both estimators pure/deterministic; always low<=high; never raise on duration<=0,
   unknown codec/resolution/CRF (fallback defaults).
2. size clamped to [0.04x,3.0x] of source_bytes; time clamped [5s,6h]; always a range.
3. format_estimate returns "" for a zero/absent estimate, else the line above.
4. encode_progress card shows the estimate line when the process has one, omits it otherwise.
5. five gates green; snapshot updated; BOT_VERSION 2.6.0.

UI GATE THAT APPLIES: #2 (ZERO new taps — estimate rides the existing card),
#4 live feedback, #6 length sanity (one line), #8 consistency (reuse
progress_card.card extra_lines + format.humanbytes), #7 pair icon with label.

OUT OF SCOPE: confirm-before-encode button (adds a tap); exact 2-pass size
targeting; message effects; any new dependency.
