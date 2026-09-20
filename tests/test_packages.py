from packages import PackageItem, size_to_bytes, version_key


def test_package_item_dataclass():
    item = PackageItem(
        pid="firefox",
        name="Firefox",
        version="120.0-1",
        source="Repo",
        origin="extra",
        size="230.0 MiB",
    )
    assert item.pid == "firefox"
    assert item.size == "230.0 MiB"


def test_size_to_bytes_binary_units():
    assert size_to_bytes("512 KiB") == 512 * 1024
    assert size_to_bytes("12.3 MiB") == 12.3 * (1024 ** 2)
    assert size_to_bytes("1 GiB") == 1024 ** 3


def test_size_to_bytes_decimal_units():
    assert size_to_bytes("2 KB") == 2 * 1000
    assert size_to_bytes("1,5 MB") == 1.5 * (1000 ** 2)
    assert size_to_bytes("3 GB") == 3 * (1000 ** 3)


def test_size_to_bytes_plain_bytes():
    assert size_to_bytes("1024 B") == 1024
    assert size_to_bytes("0") == 0.0


def test_size_to_bytes_invalid_input():
    assert size_to_bytes("") == 0.0
    assert size_to_bytes("unknown") == 0.0
    assert size_to_bytes("   ") == 0.0


def test_version_key_numeric_ordering():
    assert version_key("1.10.0") > version_key("1.9.1")
    assert version_key("1.10") > version_key("1.2")
    assert version_key("10") > version_key("2")


def test_version_key_letter_case_insensitive():
    assert version_key("1.0a") == version_key("1.0A")


def test_version_key_empty():
    assert version_key("") == ()
