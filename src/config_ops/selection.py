# Windows selection menu with the look of the shared menu frame, which needs termios.
from __future__ import annotations

import ctypes
import msvcrt
import random
import sys
import time
from collections.abc import Sequence

from .output import Printer

# Gradient stops of the frame: pink, gold, turquoise
TINTS = ((255, 45, 145), (255, 190, 65), (45, 220, 195))
# Look of the marker dot, which keeps changing color
DOTS = "🔴🟠🟡🟢🔵🟣"
INTERVAL_SECONDS = 0.4
POLL_SECONDS = 0.02
BOLD = "\033[1m"
DIM = "\033[2m"
# Console mode flag and handle id for ANSI escape sequences on stdout
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
STD_OUTPUT_HANDLE = -11
# Key names returned by _read_key
UP_KEYS = {"up", "k"}
DOWN_KEYS = {"down", "j"}


# Pick one item with the arrow keys; None when cancelled or there is no terminal
def choose(printer: Printer, title: str, items: Sequence[str]) -> int | None:
    stream = printer.stream
    if not (sys.stdin.isatty() and stream.isatty()):
        return None
    _enable_ansi()
    # Inner width fits the title, the widest item behind its dot, and the footer
    digits = len(str(len(items)))
    width = max(
        cell_width(title),
        cell_width(" / · ↑↓ · Enter · q ") + 2 * digits - 2,
        *(cell_width(item) + 3 for item in items),
    )

    def paint(text: str, style: str) -> str:
        return f"{style}{text}\033[0m" if printer.color else text

    # Content line padded to the inner width; a dot marker takes three cells
    def entry(marker: str, text: str, style: str) -> tuple[str, str, int, str]:
        padding = " " * (width - cell_width(text) - (3 if marker else 0) + 2)
        return "│  ", marker + paint(text, style) + padding, 0, "│"

    def draw(selected: int, dot: str) -> None:
        footer = f" {selected + 1:>{digits}}/{len(items)} · ↑↓ · Enter · q "
        rows = [("╭", "", width + 4, "╮"), entry("", title, BOLD), ("├", "", width + 4, "┤")]
        # The dot marks the current item; the others fade
        rows += [
            entry(f"{dot} ", item, BOLD) if index == selected else entry("⚪ ", item, DIM)
            for index, item in enumerate(items)
        ]
        rows.append(("╰─", footer, width + 3 - cell_width(footer), "╯"))
        for index, (left, text, dashes, right) in enumerate(rows):
            tint = _tint(index, len(rows) - 1)
            stream.write(f"\r\033[K{paint(left, tint)}{text}{paint('─' * dashes + right, tint)}\n")
        stream.flush()

    selected = 0
    dot = 0
    stream.write("\033[?25l")
    try:
        while True:
            # Never the same color twice in a row
            dot = (dot + random.randrange(1, len(DOTS))) % len(DOTS)
            draw(selected, DOTS[dot])
            key = _read_key(INTERVAL_SECONDS)
            # Back to the top border for the redraw or the cleanup
            stream.write(f"\033[{len(items) + 4}A")
            if key in UP_KEYS:
                selected = (selected - 1) % len(items)
            elif key in DOWN_KEYS:
                selected = (selected + 1) % len(items)
            elif key == "\r":
                return selected
            elif key in ("q", "\x1b"):
                return None
            elif key == "\x03":
                raise KeyboardInterrupt
    finally:
        stream.write("\033[J\033[?25h")
        stream.flush()


# Frame color of one line, blended across the gradient stops
def _tint(line: int, last: int) -> str:
    percent = 100 * line // max(last, 1)
    start, end = TINTS[:2] if percent <= 50 else TINTS[1:]
    mix = min((percent if percent <= 50 else percent - 50) * 2, 100)
    red, green, blue = (a + (b - a) * mix // 100 for a, b in zip(start, end, strict=True))
    return f"\033[38;2;{red};{green};{blue}m"


# Terminal cells of a text; emojis take two
def cell_width(text: str) -> int:
    return sum(2 if ord(character) >= 0x1F000 else 1 for character in text)


# Next key within the timeout; None when no key arrived, so the dot can change color
def _read_key(timeout_seconds: float) -> str | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if msvcrt.kbhit():  # pyright: ignore[reportAttributeAccessIssue]
            character = msvcrt.getwch()  # pyright: ignore[reportAttributeAccessIssue]
            # Arrow keys arrive as a prefix character plus a code
            if character in ("\x00", "\xe0"):
                code = msvcrt.getwch()  # pyright: ignore[reportAttributeAccessIssue]
                return {"H": "up", "P": "down"}.get(code, "")
            return character
        time.sleep(POLL_SECONDS)
    return None


# Let the console interpret ANSI escape sequences for colors and cursor moves
def _enable_ansi() -> None:
    kernel32 = ctypes.windll.kernel32  # pyright: ignore[reportAttributeAccessIssue]
    handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
    mode = ctypes.c_uint32()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
