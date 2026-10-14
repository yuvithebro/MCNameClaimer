import pytest

from mcclaimer.validation import validate_username


@pytest.mark.parametrize("name", ["abc", "Notch", "a_b_c", "ABCDEFGHIJKLMNOP", "123", "___"])
def test_accepts_valid_usernames(name):
    assert validate_username(name) is None


@pytest.mark.parametrize(
    ("name", "fragment"),
    [
        ("", "Enter a username"),
        ("ab", "at least 3"),
        ("ABCDEFGHIJKLMNOPQ", "at most 16"),
        ("bad name", "Only letters"),
        ("dash-name", "Only letters"),
        ("émile", "Only letters"),
        ("name!", "Only letters"),
    ],
)
def test_rejects_invalid_usernames_with_reason(name, fragment):
    assert fragment in validate_username(name)
