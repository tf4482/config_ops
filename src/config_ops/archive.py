# Move media files into YYYY/MM/DD folders by creation date.
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from .config import ArchiveSet
from .output import GREEN, RED, Printer


# Run all tasks of a set; a failed task does not stop the others
def run_archive_set(printer: Printer, archive_set: ArchiveSet) -> int:
    failed = 0
    moved_total = 0
    for task in archive_set.tasks:
        printer.line("🗃️", f"{task.source} → {task.target}")
        try:
            moved = archive_media(
                printer, Path(task.source), Path(task.target), archive_set.extensions
            )
        except (OSError, ValueError) as error:
            printer.line(
                "❌", f"{task.source} → {task.target} · {' '.join(str(error).split())}", RED
            )
            failed += 1
        else:
            moved_total += moved
            printer.line("✅", f"{moved} file(s) moved", GREEN)
    printer.line(
        "📋", f"{len(archive_set.tasks) - failed} succeeded · {failed} failed · {moved_total} moved"
    )
    return 1 if failed else 0


# Move matching files below source into dated folders below target
def archive_media(printer: Printer, source: Path, target: Path, extensions: frozenset[str]) -> int:
    validate_paths(source, target)
    moved = 0
    for path in source.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        folder = dated_folder(target, creation_time(path))
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / path.name
        remove_destination(destination)
        shutil.move(str(path), str(destination))
        moved += 1
        printer.line("📦", f"{path.name} → {destination}")
    return moved


# The source must exist, and the target must not be inside it
def validate_paths(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"archive source does not exist: {source}")
    if not source.is_dir():
        raise NotADirectoryError(f"archive source is not a directory: {source}")
    source_path = source.resolve()
    target_path = target.resolve()
    if target_path == source_path or source_path in target_path.parents:
        raise ValueError(
            f"archive target must not be the source or a folder below it: {source} → {target}"
        )


# Delete an existing destination file; never a directory
def remove_destination(destination: Path) -> None:
    if not destination.exists() and not destination.is_symlink():
        return
    if destination.is_dir() and not destination.is_symlink():
        raise IsADirectoryError(f"archive destination is an existing directory: {destination}")
    destination.unlink()


# Folder target/YYYY/MM/DD for a date
def dated_folder(target: Path, creation_date: datetime) -> Path:
    return target / f"{creation_date:%Y}" / f"{creation_date:%m}" / f"{creation_date:%d}"


# Filesystem creation time as local datetime
def creation_time(path: Path) -> datetime:
    stat_result = path.stat()
    timestamp = getattr(stat_result, "st_birthtime", stat_result.st_ctime)
    return datetime.fromtimestamp(timestamp).astimezone()
