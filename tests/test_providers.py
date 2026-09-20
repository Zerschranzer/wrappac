from providers import _parse_pacman_query_output


def test_parse_tab_separated_with_repo():
    out = "firefox\t120.0-1\textra\nlinux\t6.6.1\tcore\n"
    assert _parse_pacman_query_output(out) == [
        ("firefox", "120.0-1", "extra"),
        ("linux", "6.6.1", "core"),
    ]


def test_parse_space_separated_fallback():
    out = "firefox 120.0-1 extra\n"
    assert _parse_pacman_query_output(out) == [("firefox", "120.0-1", "extra")]


def test_parse_two_columns_without_repo():
    out = "yay\t12.0-1\n"
    assert _parse_pacman_query_output(out) == [("yay", "12.0-1", None)]


def test_parse_skips_blank_and_malformed_lines():
    out = "\n\nfirefox\t120.0-1\textra\n\ntoo-short\n\n"
    assert _parse_pacman_query_output(out) == [("firefox", "120.0-1", "extra")]


def test_parse_empty_input():
    assert _parse_pacman_query_output("") == []
