from bot.utils.ui import progress_bar, truncate


def test_progress_bar_is_clamped_and_stable_width():
    assert progress_bar(-20, 10) == "▱" * 10
    assert progress_bar(50, 10) == "▰" * 5 + "▱" * 5
    assert progress_bar(120, 10) == "▰" * 10
    assert len(progress_bar(37.5, 16)) == 16


def test_truncate_preserves_account_for_ellipsis():
    assert truncate("short", 10) == "short"
    assert truncate("1234567890", 5) == "1234…"
