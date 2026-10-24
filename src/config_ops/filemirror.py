# Mirror of a single file: copy it when the target is missing or differs.
from __future__ import annotations

import shutil
from pathlib import Path

from .config import FileTask
from .robocopy import Run, Stats

# Exit codes in the style of Robocopy: copied, nothing to do, fatal error
COPIED_EXIT_CODE = 1
UNCHANGED_EXIT_CODE = 0
ERROR_EXIT_CODE = 16
# Timestamps of network shares are only accurate to about two seconds
MTIME_TOLERANCE_SECONDS = 2


# Copy the source over the target; errors become a failed run, not an exception
def mirror_file(task: FileTask) -> Run:
    source = Path(task.source)
    target = Path(task.target)
    try:
        if not source.is_file():
            raise FileNotFoundError(f"source is not a file: {source}")
        if target.is_dir():
            raise IsADirectoryError(f"target is a folder: {target}")
        if _up_to_date(source, target, overwrite=task.overwrite):
            return Run(UNCHANGED_EXIT_CODE, (), Stats(1, 0, 1, 0, 0, 0))
        target.parent.mkdir(parents=True, exist_ok=True)
        # copy2 keeps the modification time, so the next run sees equal files
        shutil.copy2(source, target)
    except OSError as error:
        message = f"{' '.join(str(error).split())}"
        return Run(ERROR_EXIT_CODE, (message,), Stats(1, 0, 0, 0, 1, 0))
    return Run(COPIED_EXIT_CODE, (), Stats(1, 1, 0, 0, 0, 0))


# An existing target is kept when overwriting is off, or when size and time match
def _up_to_date(source: Path, target: Path, *, overwrite: bool) -> bool:
    if not target.exists():
        return False
    if not overwrite:
        return True
    source_stat = source.stat()
    target_stat = target.stat()
    return (
        source_stat.st_size == target_stat.st_size
        and abs(source_stat.st_mtime - target_stat.st_mtime) <= MTIME_TOLERANCE_SECONDS
    )
