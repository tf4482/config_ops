# Shared progress frame: transient spinner and bar for long phases.
# Copy unchanged; needs Printer from output.py.
from __future__ import annotations

import shutil
import threading
from types import TracebackType

from .output import CYAN, Printer

# Look of the progress line
CELLS = 20
INTERVAL_SECONDS = 0.1
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


# Live progress line; silent when output is not a terminal
class Progress:
    # Start a bar for a phase of total steps; use as a context manager
    def __init__(self, printer: Printer, label: str, total: int) -> None:
        self._printer = printer
        self._label = label
        self._total = total
        self._current = 0
        self._message = ""
        self._frame = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> Progress:
        if self._printer.stream.isatty():
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

    # Redraw in place, cut to the terminal width so it never wraps
    def _draw(self) -> None:
        if self._thread is None:
            return
        filled = CELLS * min(self._current, self._total) // max(self._total, 1)
        bar = "━" * filled + "─" * (CELLS - filled)
        spinner = FRAMES[self._frame % len(FRAMES)]
        text = f"{spinner} {self._label} {bar} {self._current}/{self._total}"
        if self._message:
            text += f" · {self._message}"
        text = text[: shutil.get_terminal_size().columns - 1]
        if self._printer.color:
            text = f"\033[{CYAN}m{text}\033[0m"
        self._printer.stream.write(f"\r\033[K{text}")
        self._printer.stream.flush()
