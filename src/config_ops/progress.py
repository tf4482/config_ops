# Shared progress frame: transient spinner and bar for long phases.
# Copy unchanged; needs Printer from output.py.
from __future__ import annotations

import contextlib
import os
import threading
from types import TracebackType

from .output import CYAN, Printer, tint

# Look of the progress line
CELLS = 48
INTERVAL_SECONDS = 0.1
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


# Live progress line; silent when output is not a terminal
class Progress:
    # Start a bar for a phase of total steps; use as a context manager
    def __init__(self, printer: Printer, label: str, total: int) -> None:
        self._printer = printer
        self._label = label
        self._total = total
        self._width = 80
        self._current = 0
        self._message = ""
        self._frame = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> Progress:
        if self._printer.stream.isatty():
            with contextlib.suppress(OSError):
                self._width = os.get_terminal_size(self._printer.stream.fileno()).columns or 80
            self._thread = threading.Thread(target=self._animate, daemon=True)
            self._thread.start()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.done()

    # Count one finished step; safe to call from threads
    def step(self, message: str = "") -> None:
        with self._lock:
            self._current += 1
            self._message = message
            self._draw()

    # Erase the line so normal output can continue
    def done(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join()
        self._thread = None
        self._printer.stream.write("\r\033[K")
        self._printer.stream.flush()

    # Keep the spinner moving while steps take long
    def _animate(self) -> None:
        while True:
            with self._lock:
                self._draw()
                self._frame += 1
            if self._stop.wait(INTERVAL_SECONDS):
                return

    # Redraw the progress line within the available terminal width
    def _draw(self) -> None:
        if self._thread is None:
            return
        spinner = FRAMES[self._frame % len(FRAMES)]
        current = max(0, min(self._current, self._total))
        percent = 100 * current // max(self._total, 1)
        count = f"{self._current}/{self._total}"
        limit = self._width - 1

        # Prioritize the count over the label on narrow terminals
        if limit < len(f"{spinner}    100% · {count}") + 6:
            compact = f"{spinner} · {count}"
            if (space := limit - len(compact) - 1) > 0:
                compact = f"{spinner} {self._label[:space]} · {count}"
            text = compact[: max(0, limit)]
            if self._printer.color:
                text = f"\033[{CYAN}m{text}\033[0m"
            self._write(text)
            return

        # Reserve space for the count before fitting the label and bar
        fixed = len(f"{spinner}     100% · {count}")
        label = self._label[: min(20, max(0, limit - fixed - 6))]
        bar_width = min(CELLS, limit - fixed - len(label))
        filled = bar_width * current // max(self._total, 1)
        message = ""
        remaining = limit - fixed - len(label) - bar_width
        if self._message and remaining > 3:
            message = f" · {self._message[: remaining - 3]}"

        active = "━" * filled
        inactive = "─" * (bar_width - filled)
        text = f"{spinner} {label}  {active}{inactive}  {percent:3d}% · {count}{message}"
        if self._printer.color:
            shade = tint(current, self._total)
            text = (
                f"\033[{CYAN}m{spinner}\033[0m {label}  "
                f"{shade}{active}\033[0m\033[2m{inactive}\033[0m  "
                f"{shade}{percent:3d}%\033[0m · {count}{message}"
            )
        self._write(text)

    def _write(self, text: str) -> None:
        self._printer.stream.write(f"\r\033[K{text}")
        self._printer.stream.flush()
