from datetime import datetime, time

from database import clean_value, restore_value


def test_time_roundtrip():
    t = time(13, 45)
    assert restore_value(clean_value(t)) == t


def test_datetime_roundtrip():
    dt = datetime(2026, 8, 22, 10, 30, 0)
    assert restore_value(clean_value(dt)) == dt


def test_nested_structures():
    data = {
        "a": [1, 2, {"b": time(1, 2)}],
        "c": {"d": datetime(2020, 1, 1)},
        "e": "plain",
    }
    cleaned = clean_value(data)
    # BSON-safe: everything is dict/list/str/int
    import json

    json.dumps(cleaned)
    restored = restore_value(cleaned)
    assert restored["a"][2]["b"] == time(1, 2)
    assert restored["c"]["d"] == datetime(2020, 1, 1)
    assert restored["e"] == "plain"


def test_plain_values_untouched():
    for v in (5, "s", None, True, 3.14):
        assert clean_value(v) == v
        assert restore_value(v) == v
