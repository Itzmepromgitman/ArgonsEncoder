from bot.utils import progress_card
from bot.utils.ui import progress_bar


def test_stage_chips_marks_progress_and_pending():
    chips = progress_card.stage_chips("Encoding")
    assert "✅Queued" in chips
    assert "✅Downloading" in chips
    assert "▶️Encoding" in chips
    assert chips.count("○") == 2
    assert "▶️" not in chips.replace("▶️Encoding", "")


def test_stage_chips_first_and_last_stage():
    assert progress_card.stage_chips("Queued").startswith("▶️Queued")
    done = progress_card.stage_chips("Done")
    assert "○" not in done
    assert done.endswith("▶️Done")


def test_stage_chips_unknown_stage_is_empty():
    assert progress_card.stage_chips("Teleporting") == ""


def test_headline_uses_shared_bar_and_percent():
    line = progress_card.headline(42.5)
    assert progress_bar(42.5, width=20) in line
    assert "<b>42.5%</b>" in line


def test_headline_clamps_out_of_range_percent():
    assert "100.0%" in progress_card.headline(150)
    assert "0.0%" in progress_card.headline(-5)


def test_size_line_formats_both_ends():
    assert progress_card.size_line(1024, 2048) == "📦 1.00 KB / 2.00 KB"


def test_size_line_appends_note():
    assert progress_card.size_line(0, 0, " · 🗜 2.0× smaller").endswith(
        " · 🗜 2.0× smaller"
    )


def test_timing_line_order_speed_elapsed_eta():
    line = progress_card.timing_line("2.3x", "00:12", "00:31")
    assert line == "⚡ 2.3x · ⏱ 00:12 · ⏳ ETA 00:31"


def test_progress_block_skips_empty_lines():
    block = progress_card.progress_block(10, "size", "", ["", "extra"])
    assert block.startswith("<blockquote>")
    assert block.endswith("</blockquote>")
    assert "size" in block and "extra" in block
    assert "\n\n" not in block


def test_card_assembles_skeleton_in_order():
    text = progress_card.card(
        title="🎬 Encoding",
        step_info=" · Step 1/2",
        stage="Encoding",
        subtitle="📁 <code>clip.mp4</code>",
        percent=42.5,
        size_text="📦 4.2 MiB / 10 MiB",
        timing_text="⚡ 2.3x · ⏱ 00:12 · ⏳ ETA 00:31",
        extra_lines=["🎞 30.0 fps · 📊 1234kb/s"],
        footer_lines=["<blockquote>🆔 <code>abc123</code></blockquote>"],
    )
    lines = text.splitlines()
    assert lines[0] == "<b>🎬 Encoding</b> · Step 1/2"
    assert lines[1].startswith("✅Queued")
    assert lines[2] == "📁 <code>clip.mp4</code>"
    assert lines[3].startswith("<blockquote><code>")
    assert "🎞 30.0 fps · 📊 1234kb/s" in text
    assert text.endswith("<blockquote>🆔 <code>abc123</code></blockquote>")


def test_card_omits_stage_and_subtitle_when_absent():
    text = progress_card.card("Downloading", 0, "📦 0 B / 0 B")
    assert "○" not in text
    assert "<blockquote>" in text


def test_transfer_stage_maps_direction():
    assert progress_card.transfer_stage("📥 Downloading...") == "Downloading"
    assert progress_card.transfer_stage("📤 Uploading 720p…") == "Delivering"
    assert progress_card.transfer_stage("Working") == ""
