# Shared output frame: colored lines and the startup banner.
# Copy unchanged.
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TextIO

from .configfile import PROJECT_NAME

# ANSI colors of the shared output language
RED = "31"
GREEN = "32"
YELLOW = "33"
CYAN = "36"


# Colored line output
@dataclass(slots=True)
class Printer:
    stream: TextIO
    color: bool

    # Color only on a terminal without NO_COLOR
    @classmethod
    def create(cls, stream: TextIO) -> Printer:
        return cls(stream, stream.isatty() and "NO_COLOR" not in os.environ)

    # Framed startup banner
    def header(self) -> None:
        # Rocket is two cells wide everywhere, unlike the gear
        title = f"  🚀  {PROJECT_NAME} — starting  "
        border = "─" * (len(title) + 1)
        print(file=self.stream)
        for line in (f"╭{border}╮", f"│{title}│", f"╰{border}╯"):
            self._print(line, CYAN)

    def line(self, icon: str, text: str, color: str = "") -> None:
        self._print(f"{icon} {text}", color)

    # Categorized error line; returns the failure exit code
    def fail(self, category: str, error: object) -> int:
        self.line("❌", f"{category} error: {error}", RED)
        return 1

    def _print(self, text: str, color: str) -> None:
        if self.color and color:
            text = f"\033[{color}m{text}\033[0m"
        print(text, file=self.stream)
