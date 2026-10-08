import pytest

from glue_doctor.versions import (
    LATEST_KNOWN,
    format_version,
    parse_glue_version,
    resolve_timeout,
    runtime_of,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("5.1", (5, 1)),
        ("6.0", (6, 0)),
        ("4", (4, 0)),
        (" 3.0 ", (3, 0)),
        ("7.2", (7, 2)),
        ("5.1.2", (5, 1)),
        (None, None),
        ("", None),
        ("abc", None),
        ("5.x", None),
    ],
)
def test_parse_glue_version(raw, expected):
    assert parse_glue_version(raw) == expected


def test_format_version():
    assert format_version((5, 1)) == "5.1"


def test_runtime_of_known_and_unknown():
    assert runtime_of((6, 0)) == ("4.1.1", "3.13")
    assert runtime_of((5, 1)) == ("3.5.6", "3.11")
    assert runtime_of((9, 9)) is None


def test_latest_known_is_6_0():
    assert LATEST_KNOWN == (6, 0)


def test_explicit_timeout_wins():
    assert resolve_timeout(60, (5, 1)) == (60, "explicit")


@pytest.mark.parametrize(
    "version, minutes",
    [((6, 0), 480), ((5, 1), 480), ((5, 0), 480), ((4, 0), 2880), ((3, 0), 2880), ((7, 0), 480)],
)
def test_default_timeout_by_version(version, minutes):
    assert resolve_timeout(None, version) == (minutes, "default")


def test_unknown_version_assumes_legacy_default():
    assert resolve_timeout(None, None) == (2880, "assumed-default")
