# Zip of a folder: pack all files into one archive, then move it into the target folder.
from __future__ import annotations

import fnmatch
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from .config import FileTask, RobocopySettings
from .robocopy import Run, Stats

# Exit codes in the style of Robocopy: copied, nothing to do, fatal error
COPIED_EXIT_CODE = 1
UNCHANGED_EXIT_CODE = 0
ERROR_EXIT_CODE = 16


# Zip the source folder into the target folder; errors become a failed run, not an exception.
# Excludes of the task add to the global ones.
def zip_folder(task: FileTask, settings: RobocopySettings | None = None) -> Run:
    settings = settings or RobocopySettings()
    exclude_dirs = (*settings.exclude_dirs, *task.exclude_dirs)
    exclude_files = (*settings.exclude_files, *task.exclude_files)
    source = Path(task.source)
    archive = Path(task.target) / f"{source.name}.zip"
    try:
        if not source.is_dir():
            raise NotADirectoryError(f"source is not a folder: {source}")
        if archive.is_dir():
            raise IsADirectoryError(f"archive path is a folder: {archive}")
        if archive.exists() and not task.overwrite:
            return Run(UNCHANGED_EXIT_CODE, (), Stats(1, 0, 1, 0, 0, 0))
        archive.parent.mkdir(parents=True, exist_ok=True)
        # Build the archive locally first, so a broken run never leaves a partial target
        with tempfile.TemporaryDirectory() as temp:
            local = Path(temp) / archive.name
            count = _pack(source, local, exclude_dirs, exclude_files)
            archive.unlink(missing_ok=True)
            shutil.move(local, archive)
    except OSError as error:
        message = " ".join(str(error).split())
        return Run(ERROR_EXIT_CODE, (message,), Stats(1, 0, 0, 0, 1, 0))
    return Run(COPIED_EXIT_CODE, (), Stats(count, count, 0, 0, 0, 0))


# Name matches one of the wildcard patterns, ignoring case like Windows
def _excluded(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in patterns)


# Write every file that is not excluded into the archive; returns the file count
def _pack(
    source: Path, archive: Path, exclude_dirs: tuple[str, ...], exclude_files: tuple[str, ...]
) -> int:
    count = 0
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for folder, dirs, files in os.walk(source):
            # Pruned folders are not entered at all
            dirs[:] = sorted(d for d in dirs if not _excluded(d, exclude_dirs))
            for name in sorted(files):
                if not _excluded(name, exclude_files):
                    path = Path(folder) / name
                    bundle.write(path, path.relative_to(source))
                    count += 1
    return count
