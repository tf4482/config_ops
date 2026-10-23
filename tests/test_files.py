from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import Mock

import pytest

from config_ops import files
from config_ops.config import FileSet, FileTask
from config_ops.output import Printer

UNC = r"\\server.example\main\data"


def capture() -> tuple[Printer, io.StringIO]:
    stream = io.StringIO()
    return Printer(stream, color=False), stream


def sync_task(**changes: object) -> FileTask:
    values = {
        "kind": "sync",
        "source": r"C:\a",
        "target": r"C:\b",
        "overwrite": True,
        "options": None,
    }
    return FileTask(**(values | changes))  # pyright: ignore[reportArgumentType]


def test_unmapped_unc_alias_is_rejected() -> None:
    file_set = FileSet(False, None, (sync_task(kind="mirror", target=UNC),))

    with pytest.raises(files.FilesError, match="no matching smb mapping"):
        files.run_file_set(capture()[0], file_set, None)


def test_robocopy_accepts_full_unc_path_without_drive_mapping() -> None:
    command = files.build_robocopy_command(UNC, r"C:\backup", ("/E",), overwrite=True)

    assert command[:4] == ["robocopy", UNC, r"C:\backup", "/E"]


def test_sync_options_never_delete_and_keep_the_newer_file() -> None:
    options = files.task_options(sync_task(), ("/MIR", "/XC", "/R:1"))

    assert options == ("/E", "/XO", "/R:1")


def test_sync_creates_missing_root_and_rejects_nested_roots(tmp_path: Path) -> None:
    source = tmp_path / "source"

    files.prepare_sync_folders(str(source), str(tmp_path / "target"))
    assert source.is_dir() and (tmp_path / "target").is_dir()

    with pytest.raises(files.FilesError, match="must not contain one another"):
        files.prepare_sync_folders(str(source), str(source / "nested"))


def test_robocopy_oem_output_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    process = Mock(stdout=iter(["Robustes Dateikopieren für Windows\n"]))
    process.wait.return_value = 0
    process.poll.return_value = 0
    popen = Mock(return_value=process)
    monkeypatch.setattr(files.subprocess, "Popen", popen)
    printer, stream = capture()

    assert files.invoke_robocopy(printer, r"C:\a", r"C:\b", ("/E",)) == 0
    assert popen.call_args.kwargs["encoding"] == "oem"
    assert "Robustes Dateikopieren für Windows" in stream.getvalue()
