from __future__ import annotations

import io
from pathlib import Path

import pytest

from config_ops import files
from config_ops.config import FileSet, FileTask
from config_ops.output import Printer
from config_ops.robocopy import Run, Stats

UNC = r"\\server.example\main\data"
STATS = Stats(files=719, copied=1, skipped=718, mismatched=0, failed=0, extras=3)


def capture() -> tuple[Printer, io.StringIO]:
    stream = io.StringIO()
    return Printer(stream, color=False), stream


def task(kind: str, **changes: object) -> FileTask:
    values = {
        "kind": kind,
        "source": r"C:\a",
        "target": r"C:\b",
        "overwrite": True,
        "options": None,
    }
    return FileTask(**(values | changes))  # pyright: ignore[reportArgumentType]


def test_unmapped_unc_alias_is_rejected() -> None:
    file_set = FileSet(False, None, (task("mirror", target=UNC),))

    with pytest.raises(files.FilesError, match="no matching smb mapping"):
        files.run_file_set(capture()[0], file_set, None)


def test_sync_creates_missing_root_and_rejects_nested_roots(tmp_path: Path) -> None:
    source = tmp_path / "source"

    files.prepare_sync_folders(str(source), str(tmp_path / "target"))
    assert source.is_dir() and (tmp_path / "target").is_dir()

    with pytest.raises(files.FilesError, match="must not contain one another"):
        files.prepare_sync_folders(str(source), str(source / "nested"))


def test_set_reports_counters_and_fails_on_a_robocopy_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = iter([Run(1, (), STATS), Run(8, ("ERROR 5 Access is denied",), None)])
    monkeypatch.setattr(files, "run_task", lambda *_args: next(runs))
    printer, stream = capture()
    file_set = FileSet(False, None, (task("mirror"), task("copy")))

    assert files.run_file_set(printer, file_set, None) == 1

    text = stream.getvalue()
    assert "✅ mirror · 719 files · 1 copied · 718 unchanged · 3 removed" in text
    assert "❌ copy · exit code 8" in text
    assert "ERROR 5 Access is denied" in text
    assert "1 succeeded · 1 failed" in text


def test_counters_are_colored_on_a_terminal() -> None:
    printer = Printer(io.StringIO(), color=True)

    parts = files._counters(printer, "mirror", STATS)

    assert "\033[32m1 copied" in parts[1]
    assert "\033[33m3 removed" in parts[3]


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_terminal_run_prints_the_lines_after_the_progress_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(files, "run_task", lambda *_args: Run(0, (), STATS))
    stream = Terminal()
    file_set = FileSet(False, None, (task("mirror"), task("copy")))

    assert files.run_file_set(Printer(stream, color=False), file_set, None) == 0

    text = stream.getvalue()
    assert text.rstrip().endswith("2 succeeded · 0 failed")
    assert text.index("📦 mirror") < text.index("✅ mirror") < text.index("📦 copy")
