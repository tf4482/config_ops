# Robocopy sets: mirror, copy, move, and bidirectional sync.
from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import FileSet, FileTask, SmbSettings
from .filemirror import mirror_file
from .output import CYAN, GREEN, RED, YELLOW, Printer
from .progress import Progress
from .robocopy import FAILURE_EXIT_CODE, MAX_ERROR_LINES, Run, Stats, error_lines, run_task
from .smb import Unreachable, blocked_by, report_skipped, unc_path_covered, unc_share_root

# Dim text for counters that carry no news
DIM = "2"
# Robocopy exit code bit for mismatched files
MISMATCH_EXIT_CODE = 4


# Invalid file task or folder layout
class FilesError(RuntimeError):
    pass


# A finished task with its run and duration; skipped names the unreachable share
@dataclass(frozen=True, slots=True)
class TaskResult:
    task: FileTask
    run: Run
    seconds: float
    skipped: str | None = None

    @property
    def failed(self) -> bool:
        return self.run.return_code >= FAILURE_EXIT_CODE


# Run all tasks of a set in order; tasks on unreachable shares are skipped, not failed
def run_file_set(
    printer: Printer,
    file_set: FileSet,
    smb: SmbSettings | None,
    unreachable: Sequence[Unreachable] = (),
) -> int:
    _check_unc_paths(file_set, smb)
    _prepare_all_sync_folders(file_set, unreachable)
    # Without a terminal there is no bar, so every task reports as soon as it is done
    live = not printer.stream.isatty()
    results: list[TaskResult] = []
    with Progress(printer, "Robocopy", len(file_set.tasks)) as bar:
        for task in file_set.tasks:
            if live:
                _announce(printer, task)
            blocked = blocked_by(unreachable, task.source, task.target)
            started = time.monotonic()
            if blocked is None:
                results.append(TaskResult(task, _run(task, file_set), time.monotonic() - started))
            else:
                results.append(TaskResult(task, Run(0, (), None), 0.0, blocked.label))
            if live:
                report(printer, results[-1])
            bar.step(f"{task.kind} · {Path(task.source).name or task.source}")
    # The bar is gone now, so the lines of a terminal run can follow
    if not live:
        for result in results:
            _announce(printer, result.task)
            report(printer, result)
    failed = sum(result.failed for result in results)
    skipped = [result.skipped for result in results if result.skipped]
    summary = f"{len(results) - failed - len(skipped)} succeeded · {failed} failed"
    printer.line("📋", summary + (f" · {len(skipped)} skipped" if skipped else ""))
    report_skipped(printer, skipped)
    return 1 if failed else 0


# One result line with colored counters; Robocopy's own output only for failures
def report(printer: Printer, result: TaskResult) -> None:
    kind = result.task.kind
    code = result.run.return_code
    if result.skipped:
        printer.line("⏭️", f"{kind} · skipped · {result.skipped} unreachable", YELLOW)
        return
    if result.failed:
        parts = [kind, f"exit code {code}", *_counters(printer, kind, result.run.stats)]
        printer.line("❌", " · ".join([*parts, f"{result.seconds:.1f}s"]), RED)
        details = error_lines(result.run.lines)
        for line in details[:MAX_ERROR_LINES]:
            printer.line("📄", line.strip())
        if len(details) > MAX_ERROR_LINES:
            printer.line("📄", f"… {len(details) - MAX_ERROR_LINES} more line(s)")
        return
    parts = [kind, *_counters(printer, kind, result.run.stats), f"{result.seconds:.1f}s"]
    if result.run.stats is None:
        parts.insert(1, f"exit code {code}")
    printer.line("⚠️" if code & MISMATCH_EXIT_CODE else "✅", " · ".join(parts))


# Counters colored by meaning: news in color, the rest dim
def _counters(printer: Printer, kind: str, stats: Stats | None) -> list[str]:
    if stats is None:
        return []
    # Extras of a mirror are the files it removed
    extras_label = "removed" if kind == "mirror" else "extra"
    counters = [
        _paint(printer, f"{stats.files} file{'' if stats.files == 1 else 's'}", CYAN),
        _paint(printer, f"{stats.copied} copied", GREEN if stats.copied else DIM),
        _paint(printer, f"{stats.skipped} unchanged", DIM),
    ]
    if stats.extras:
        counters.append(_paint(printer, f"{stats.extras} {extras_label}", YELLOW))
    if stats.mismatched:
        counters.append(_paint(printer, f"{stats.mismatched} mismatched", YELLOW))
    if stats.failed:
        counters.append(_paint(printer, f"{stats.failed} failed", RED))
    return counters


# Colored segment inside a line; the printer colors whole lines only
def _paint(printer: Printer, text: str, color: str) -> str:
    return f"\033[{color}m{text}\033[0m" if printer.color else text


# One file is mirrored by copying, everything else by Robocopy
def _run(task: FileTask, file_set: FileSet) -> Run:
    if task.kind == "mirror-file":
        return mirror_file(task)
    return run_task(task, file_set.common_options)


def _announce(printer: Printer, task: FileTask) -> None:
    printer.line("📦", f"{task.kind} · {task.source} → {task.target}")


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


# Every UNC path needs a configured mapping of its share
def _check_unc_paths(file_set: FileSet, smb: SmbSettings | None) -> None:
    mappings = smb.mappings if smb and file_set.smb else ()
    for task in file_set.tasks:
        for path in (task.source, task.target):
            if not unc_path_covered(path, mappings):
                raise FilesError(f"{unc_share_root(path)} has no matching smb mapping")


# Prepare the folders of every sync task whose shares can be reached
def _prepare_all_sync_folders(file_set: FileSet, unreachable: Sequence[Unreachable]) -> None:
    for task in file_set.tasks:
        if task.kind == "sync" and blocked_by(unreachable, task.source, task.target) is None:
            prepare_sync_folders(task.source, task.target)
