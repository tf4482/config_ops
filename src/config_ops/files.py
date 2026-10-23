# Robocopy sets: mirror, copy, move, and bidirectional sync.
from __future__ import annotations

import subprocess
from pathlib import Path

from .config import FileKind, FileSet, FileTask, SmbSettings
from .output import GREEN, RED, Printer
from .smb import unc_path_covered, unc_share_root

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


# Invalid file task or folder layout
class FilesError(RuntimeError):
    pass


# Run all tasks of a set in order; returns 1 when any task failed
def run_file_set(printer: Printer, file_set: FileSet, smb: SmbSettings | None) -> int:
    _check_unc_paths(file_set, smb)
    _prepare_all_sync_folders(file_set)
    failed = 0
    for task in file_set.tasks:
        printer.line("📦", f"{task.kind} · {task.source} → {task.target}")
        return_code = _run_task(printer, task, file_set.common_options)
        if return_code >= FAILURE_EXIT_CODE:
            printer.line("❌", f"{task.kind} · exit code {return_code}", RED)
            failed += 1
        else:
            printer.line("✅", f"{task.kind} · exit code {return_code}", GREEN)
    printer.line("📋", f"{len(file_set.tasks) - failed} succeeded · {failed} failed")
    return 1 if failed else 0


# Command line of one Robocopy call
def build_robocopy_command(
    source: str, target: str, options: tuple[str, ...], *, overwrite: bool
) -> list[str]:
    effective = options if overwrite else (*options, "/XN", "/XO", "/XX")
    excludes = [item for name in EXCLUDE_DIRS for item in ("/XD", name)]
    excludes += [item for name in EXCLUDE_FILES for item in ("/XF", name)]
    return ["robocopy", source, target, *effective, *excludes]


# Options of a task: its own, or the kind defaults plus the common options
def task_options(task: FileTask, common_options: tuple[str, ...] | None) -> tuple[str, ...]:
    options = task.options or (*KIND_OPTIONS[task.kind], *(common_options or COMMON_OPTIONS))
    if task.kind != "sync":
        return options
    # Sync never deletes and keeps the newer file
    kept = tuple(option for option in options if option.upper() not in SYNC_INCOMPATIBLE_OPTIONS)
    present = {option.upper() for option in kept}
    return (*(option for option in ("/E", "/XO") if option not in present), *kept)


# Create the sync roots and reject folders that contain each other
def prepare_sync_folders(source: str, target: str) -> None:
    source_path = Path(source).resolve(strict=False)
    target_path = Path(target).resolve(strict=False)
    if source_path == target_path:
        raise FilesError(f"sync source and target must be different folders: {source_path}")
    if source_path in target_path.parents or target_path in source_path.parents:
        raise FilesError(
            f"sync folders must not contain one another: {source_path} ↔ {target_path}"
        )
    for label, path in (("source", source_path), ("target", target_path)):
        if path.exists() and not path.is_dir():
            raise FilesError(f"sync {label} is not a folder: {path}")
    source_path.mkdir(parents=True, exist_ok=True)
    target_path.mkdir(parents=True, exist_ok=True)


# Run Robocopy once and stream its output; sync runs a second pass in reverse
def invoke_robocopy(
    printer: Printer, source: str, target: str, options: tuple[str, ...], *, overwrite: bool = True
) -> int:
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
        for line in process.stdout or ():
            # Blank lines and separator rules carry no information
            if line.strip(" -\t\r\n"):
                printer.line("📄", line.rstrip())
        return process.wait()
    finally:
        # Stop Robocopy when the run is interrupted
        if process.poll() is None:
            process.terminate()
            process.wait()


# One Robocopy call; sync runs forward then reverse
def _run_task(printer: Printer, task: FileTask, common_options: tuple[str, ...] | None) -> int:
    options = task_options(task, common_options)
    if task.kind != "sync":
        return invoke_robocopy(printer, task.source, task.target, options, overwrite=task.overwrite)
    forward = invoke_robocopy(printer, task.source, task.target, options)
    if forward >= FAILURE_EXIT_CODE:
        return forward
    reverse = invoke_robocopy(printer, task.target, task.source, options)
    return reverse if reverse >= FAILURE_EXIT_CODE else forward | reverse


# Every UNC path needs a configured mapping of its share
def _check_unc_paths(file_set: FileSet, smb: SmbSettings | None) -> None:
    mappings = smb.mappings if smb and file_set.smb else ()
    for task in file_set.tasks:
        for path in (task.source, task.target):
            if not unc_path_covered(path, mappings):
                raise FilesError(f"{unc_share_root(path)} has no matching smb mapping")


# Prepare the folders of every sync task
def _prepare_all_sync_folders(file_set: FileSet) -> None:
    for task in file_set.tasks:
        if task.kind == "sync":
            prepare_sync_folders(task.source, task.target)
