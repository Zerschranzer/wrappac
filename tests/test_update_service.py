from update_service import _sanitize_time, WEEKDAYS


def test_sanitize_valid_times():
    assert _sanitize_time("09:00", "00:00") == (9, 0)
    assert _sanitize_time("23:59", "00:00") == (23, 59)
    assert _sanitize_time("9:5", "08:00") == (9, 5)


def test_sanitize_invalid_falls_back_to_default():
    assert _sanitize_time("24:00", "08:30") == (8, 30)   # hour out of range
    assert _sanitize_time("10:99", "08:30") == (8, 30)   # minute out of range
    assert _sanitize_time("not-a-time", "07:15") == (7, 15)
    assert _sanitize_time("", "06:45") == (6, 45)


def test_weekdays_order():
    assert WEEKDAYS == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
