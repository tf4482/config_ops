from __future__ import annotations

import io
from collections.abc import Iterator
from unittest.mock import Mock

import pytest

from config_ops import selection
from config_ops.output import Printer


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


# Terminal printer whose key presses are scripted
def scripted(monkeypatch: pytest.MonkeyPatch, keys: list[str | None]) -> Printer:
    presses: Iterator[str | None] = iter(keys)
    monkeypatch.setattr(selection, "_read_key", lambda _timeout: next(presses))
    monkeypatch.setattr(selection, "_enable_ansi", lambda: None)
    monkeypatch.setattr(selection.sys, "stdin", Mock(isatty=lambda: True))
    return Printer(Terminal(), color=False)


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (["down", "j", "\r"], 2),
        (["up", "\r"], 2),
        ([None, "k", "\r"], 2),
        (["down", "q"], None),
        (["\x1b"], None),
    ],
)
def test_keys_choose_an_item(
    monkeypatch: pytest.MonkeyPatch, keys: list[str | None], expected: int | None
) -> None:
    printer = scripted(monkeypatch, keys)

    assert selection.choose(printer, "Pick", ["one", "two", "three"]) == expected


def test_no_terminal_means_no_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(selection.sys, "stdin", Mock(isatty=lambda: False))

    assert selection.choose(Printer(Terminal(), color=False), "Pick", ["one"]) is None
