# Robocopy calls: command lines, execution, and the statistics of its summary table.
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from .config import FileKind, FileTask

# Robocopy reports failures with exit codes from 8 up
FAILURE_EXIT_CODE = 8
EXCLUDE_DIRS = (
    "cache",
    "shadercache",
    ".recycle",
    "$RECYCLE.BIN",
    "System Volume Information",
    "My Music",
    "My Videos",
    "My Pictures",
)
EXCLUDE_FILES = ("desktop.ini",)
COMMON_OPTIONS = (
    "/MT:32",
    "/W:2",
    "/R:10",
    "/XJ",
    "/XC",
    "/NJH",
    "/NFL",
    "/NDL",
    "/NC",
    "/NS",
    "/NP",
)
# Options that are specific to one kind of operation
KIND_OPTIONS: dict[FileKind, tuple[str, ...]] = {
    "mirror": ("/MIR",),
    "copy": ("/E",),
    "move": ("/MOV", "/E"),
    "sync": ("/E", "/XO"),
}
# Options that would make sync delete or overwrite newer files
SYNC_INCOMPATIBLE_OPTIONS = {"/MIR", "/PURGE", "/MOV", "/MOVE", "/XC", "/XN", "/XL"}
# A table row of six counters, in any language: "Dirs :  19  18  19  0  0  0"
COUNTER_ROW = re.compile(r"^\s*[^\d\s][^:]*:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$")


# File counters of one run: total, copied, skipped, mismatched, failed, extras
@dataclass(frozen=True, slots=True)
class Stats:
    files: int
    copied: int
    skipped: int
    mismatched: int
    failed: int
    extras: int


# Exit code, output lines, and statistics of one task
@dataclass(frozen=True, slots=True)
class Run:
    return_code: int
    lines: tuple[str, ...]
    stats: Stats | None


# Run the task; sync runs a forward and a reverse pass
def run_task(task: FileTask, common_options: tuple[str, ...] | None) -> Run:
    options = task_options(task, common_options)
    if task.kind != "sync":
        return invoke_robocopy(task.source, task.target, options, overwrite=task.overwrite)
    forward = invoke_robocopy(task.source, task.target, options)
    if forward.return_code >= FAILURE_EXIT_CODE:
        return forward
    reverse = invoke_robocopy(task.target, task.source, options)
    if reverse.return_code >= FAILURE_EXIT_CODE:
        return Run(reverse.return_code, forward.lines + reverse.lines, None)
    stats = _sync_stats(forward.stats, reverse.stats)
    return Run(forward.return_code | reverse.return_code, forward.lines + reverse.lines, stats)


# Both sync passes as one picture: the union of files, extras are just the other side's files
def _sync_stats(forward: Stats | None, reverse: Stats | None) -> Stats | None:
    if forward is None or reverse is None:
        return None
    files = forward.files + reverse.copied
    copied = forward.copied + reverse.copied
    return Stats(
        files,
        copied,
        files - copied,
        forward.mismatched + reverse.mismatched,
        forward.failed + reverse.failed,
        0,
    )


# Options of a task: its own, or the kind defaults plus the common options
def task_options(task: FileTask, common_options: tuple[str, ...] | None) -> tuple[str, ...]:
    options = task.options or (*KIND_OPTIONS[task.kind], *(common_options or COMMON_OPTIONS))
    if task.kind != "sync":
        return options
    # Sync never deletes and keeps the newer file
    kept = tuple(option for option in options if option.upper() not in SYNC_INCOMPATIBLE_OPTIONS)
    present = {option.upper() for option in kept}
    return (*(option for option in ("/E", "/XO") if option not in present), *kept)


# Command line of one Robocopy call
def build_robocopy_command(
    source: str, target: str, options: tuple[str, ...], *, overwrite: bool
) -> list[str]:
    effective = options if overwrite else (*options, "/XN", "/XO", "/XX")
    excludes = [item for name in EXCLUDE_DIRS for item in ("/XD", name)]
    excludes += [item for name in EXCLUDE_FILES for item in ("/XF", name)]
    return ["robocopy", source, target, *effective, *excludes]


# Run Robocopy once and keep its output; Robocopy is stopped when the run is interrupted
def invoke_robocopy(
    source: str, target: str, options: tuple[str, ...], *, overwrite: bool = True
) -> Run:
    command = build_robocopy_command(source, target, options, overwrite=overwrite)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="oem",
        errors="replace",
    )
    try:
        # Blank lines and separator rules carry no information
        lines = tuple(line.rstrip() for line in process.stdout or () if line.strip(" -\t\r\n"))
        return Run(process.wait(), lines, parse_stats(lines))
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()


# Dirs and Files rows come first in the summary table, whatever the language
def parse_stats(lines: tuple[str, ...]) -> Stats | None:
    rows = [match for line in lines if (match := COUNTER_ROW.match(line))]
    if len(rows) < 2:
        return None
    return Stats(*(int(value) for value in rows[1].groups()))
