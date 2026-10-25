from __future__ import annotations

from unittest.mock import Mock

import pytest

from config_ops import robocopy
from config_ops.config import FileTask

UNC = r"\\server.example\main\data"

GERMAN = (
    "           Insgesamt   KopiertÜbersprungenKeine Übereinstimmung    FEHLER    Extras",
    "Verzeich.:        19        18        19         0         0         0",
    "  Dateien:       719         1       718         0         0         3",
    "    Bytes:  160.00 m   1.2 k  160.00 m         0         0         0",
)
ENGLISH = (
    "               Total    Copied   Skipped  Mismatch    FAILED    Extras",
    "    Dirs :         1         1         0         0         0         0",
    "   Files :       137       137         0         0         0         0",
    "   Bytes :    2.03 m    2.03 m         0         0         0         0",
)


def sync_task(**changes: object) -> FileTask:
    values = {
        "kind": "sync",
        "source": r"C:\a",
        "target": r"C:\b",
        "overwrite": True,
        "options": None,
    }
    return FileTask(**(values | changes))  # pyright: ignore[reportArgumentType]


def test_robocopy_accepts_full_unc_path_without_drive_mapping() -> None:
    command = robocopy.build_robocopy_command(UNC, r"C:\backup", ("/E",), overwrite=True)

    assert command[:4] == ["robocopy", UNC, r"C:\backup", "/E"]


def test_sync_options_never_delete_and_keep_the_newer_file() -> None:
    options = robocopy.task_options(sync_task(), ("/MIR", "/XC", "/R:1"))

    assert options == ("/E", "/XO", "/R:1")


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (GERMAN, robocopy.Stats(719, 1, 718, 0, 0, 3)),
        (ENGLISH, robocopy.Stats(137, 137, 0, 0, 0, 0)),
        (("ERROR 5 (0x00000005) Access is denied",), None),
    ],
)
def test_statistics_are_read_from_the_summary_table(
    lines: tuple[str, ...], expected: robocopy.Stats | None
) -> None:
    assert robocopy.parse_stats(lines) == expected


def test_robocopy_oem_output_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    process = Mock(stdout=iter(["Robustes Dateikopieren für Windows\n", "-----\n", "\n"]))
    process.wait.return_value = 0
    process.poll.return_value = 0
    popen = Mock(return_value=process)
    monkeypatch.setattr(robocopy.subprocess, "Popen", popen)

    run = robocopy.invoke_robocopy(r"C:\a", r"C:\b", ("/E",))

    assert popen.call_args.kwargs["encoding"] == "oem"
    assert run.lines == ("Robustes Dateikopieren für Windows",)
    assert run.return_code == 0


def test_sync_passes_are_combined_into_one_picture() -> None:
    forward = robocopy.Stats(files=3, copied=1, skipped=2, mismatched=0, failed=0, extras=1)
    reverse = robocopy.Stats(files=4, copied=1, skipped=3, mismatched=0, failed=0, extras=0)

    assert robocopy._sync_stats(forward, reverse) == robocopy.Stats(4, 2, 2, 0, 0, 0)


def test_failures_show_only_the_error_entries_and_their_messages() -> None:
    lines = (
        r"                \\server.example\x\file1.txt",
        r"2026/10/23 14:59:55 FEHLER 5 (0x00000005) Verzeichnis wird kopiert C:\x\temp\ ",
        "Zugriff verweigert",
        r"                \\server.example\x\file2.txt",
        r"2026/10/23 14:59:56 ERROR 32 (0x00000020) Copying File C:\x\a.txt",
        "The process cannot access the file",
    )

    assert robocopy.error_lines(lines) == [lines[1], lines[2], lines[4], lines[5]]
    assert robocopy.error_lines(("Invalid Parameter #3",)) == ["Invalid Parameter #3"]


def test_excludes_become_xd_and_xf_arguments() -> None:
    command = robocopy.build_robocopy_command(
        r"C:\a",
        r"C:\b",
        ("/MIR",),
        overwrite=True,
        exclude_dirs=("cache", ".venv"),
        exclude_files=("*.pyc",),
    )

    assert command[3:] == ["/MIR", "/XD", "cache", "/XD", ".venv", "/XF", "*.pyc"]


def test_task_excludes_add_to_the_global_ones_and_set_options_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        robocopy, "invoke_robocopy", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    settings = robocopy.RobocopySettings(("/R:1",), ("cache", ".venv"), ("desktop.ini",))
    task = sync_task(
        kind="mirror", exclude_dirs=(".venv", "node_modules"), exclude_files=("*.pyc",)
    )

    robocopy.run_task(task, None, settings)
    robocopy.run_task(task, ("/R:9",), settings)

    first, second = calls
    assert first[0][2] == ("/MIR", "/R:1")
    assert second[0][2] == ("/MIR", "/R:9")
    assert first[1]["exclude_dirs"] == ("cache", ".venv", "node_modules")
    assert first[1]["exclude_files"] == ("desktop.ini", "*.pyc")
