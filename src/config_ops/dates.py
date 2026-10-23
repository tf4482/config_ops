# Set Windows file times from names or metadata, or write metadata from file times.
from __future__ import annotations

import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from . import filetime, metadata
from .config import DateSet
from .output import GREEN, RED, Printer

# Folder created below the source when files are copied without a target folder
DEFAULT_TARGET_NAME = "changed_date"


# Run one set; a failed file does not stop the others
def run_date_set(printer: Printer, date_set: DateSet) -> int:
    source = Path(date_set.source_folder)
    in_place = date_set.change_files_in_place
    target = source if in_place else Path(date_set.target_folder or source / DEFAULT_TARGET_NAME)
    if not in_place:
        target.mkdir(parents=True, exist_ok=True)
    # A target below the source is never scanned again
    excluded = target if not in_place and _is_relative_to(target, source) else None
    recursive = date_set.mode != "file"
    relative_root = source if recursive else None
    printer.line("🕒", f"{source} · {date_set.mode} mode")

    updated = skipped = failed = 0
    for path in _matching_files(source, date_set.extensions, recursive, excluded):
        try:
            timestamp = _timestamp(date_set, path)
            if timestamp is None:
                skipped += 1
                continue
            destination = _prepare_destination(
                path,
                in_place=in_place,
                target=target,
                overwrite=date_set.overwrite,
                relative_root=relative_root,
            )
            if date_set.mode == "metadata_reverse":
                written = _write_metadata(destination, timestamp)
            else:
                filetime.set_creation_and_modification_times(destination, timestamp)
                written = True
        except Exception as error:
            # One faulty file must not stop the run
            printer.line("❌", f"{path} · {' '.join(str(error).split())}", RED)
            failed += 1
        else:
            if written:
                printer.line("✅", f"{destination.name} → {timestamp:%Y-%m-%d %H:%M:%S}", GREEN)
                updated += 1
            else:
                skipped += 1
    printer.line("📋", f"{updated} updated · {skipped} skipped · {failed} failed")
    return 1 if failed else 0


# Timestamp of a file for the set's mode, shifted by the hour adjustment
def _timestamp(date_set: DateSet, path: Path) -> datetime | None:
    match date_set.mode:
        case "file":
            timestamp = parse_timestamp_text(path.stem, date_set.patterns)
        case "folder":
            timestamp = parse_timestamp_text(path.parent.name, date_set.patterns)
        case "metadata":
            timestamp = metadata.read_timestamp(path)
        case "metadata_reverse":
            timestamp = filetime.modification_time(path)
    if timestamp is None:
        return None
    return timestamp + timedelta(hours=date_set.hour_adjustment)


# First pattern that matches the text; time groups default to midnight
def parse_timestamp_text(value: str, patterns: tuple[re.Pattern[str], ...]) -> datetime | None:
    for pattern in patterns:
        match = pattern.match(value)
        if match is None:
            continue
        groups = match.groupdict()
        year = groups.get("year") or f"20{groups['year2']}"
        stamp = f"{year}{groups['month']}{groups['day']}"
        stamp += "".join(groups.get(name) or "00" for name in ("hour", "minute", "second"))
        return datetime.strptime(stamp, "%Y%m%d%H%M%S").astimezone()
    return None


# Write the metadata timestamp and restore the file times it touched
def _write_metadata(path: Path, timestamp: datetime) -> bool:
    original_times = filetime.get_file_times(path)
    try:
        return metadata.write_timestamp(path, timestamp)
    finally:
        filetime.set_raw_file_times(path, *original_times)


# The file to change: the source itself, or a copy below the target folder
def _prepare_destination(
    path: Path, *, in_place: bool, target: Path, overwrite: bool, relative_root: Path | None
) -> Path:
    if in_place:
        return path
    destination = target / (path.relative_to(relative_root) if relative_root else path.name)
    if not overwrite:
        destination = _collision_safe_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)
    return destination


# Append a numeric suffix until the path is free
def _collision_safe_path(path: Path) -> Path:
    candidate = path
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        counter += 1
    return candidate


# Files with a wanted extension, optionally recursive
def _matching_files(
    source: Path, extensions: frozenset[str], recursive: bool, excluded: Path | None
) -> list[Path]:
    paths = source.rglob("*") if recursive else source.iterdir()
    return [
        path
        for path in paths
        if path.is_file()
        and path.suffix.lower() in extensions
        and (excluded is None or not _is_relative_to(path, excluded))
    ]


# Whether path lies below root
def _is_relative_to(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())
