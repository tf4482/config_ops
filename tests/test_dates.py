from __future__ import annotations

import re
from datetime import datetime

import pytest

from config_ops.dates import parse_timestamp_text

PATTERNS = (
    re.compile(
        r"^(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})_"
        r"(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})$"
    ),
    re.compile(r"^WoW_(?P<month>\d{2})(?P<day>\d{2})(?P<year2>\d{2})$"),
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("20240315_143045", datetime(2024, 3, 15, 14, 30, 45)),
        ("WoW_031524", datetime(2024, 3, 15)),
        ("holiday", None),
    ],
)
def test_timestamp_is_parsed_from_the_name(name: str, expected: datetime | None) -> None:
    timestamp = parse_timestamp_text(name, PATTERNS)

    assert (timestamp.replace(tzinfo=None) if timestamp else None) == expected
