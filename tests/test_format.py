from bot.utils.format import format_time, humanbytes


def test_humanbytes_zero():
    assert humanbytes(0) == "0 B"
    assert humanbytes(None) == "0 B"


def test_humanbytes_units():
    assert humanbytes(512) == "512.00 B"
    assert humanbytes(1024) == "1.00 KB"
    assert humanbytes(1024 * 1024 * 5) == "5.00 MB"
    assert humanbytes(1024 ** 3) == "1.00 GB"
    assert humanbytes(1024 ** 4) == "1.00 TB"


def test_format_time():
    assert format_time(0) == "0s"
    assert format_time(-5) == "0s"
    assert format_time(45_000) == "45s"
    assert format_time(60_000) == "1m 00s"
    assert format_time(3_725_000).startswith("1h 02m")
    assert format_time(900_610_000).startswith("10d")


def test_timeformatter_alias():
    from bot.utils.format import TimeFormatter

    assert TimeFormatter(1000) == format_time(1000)
