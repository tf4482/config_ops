from __future__ import annotations

import os
from pathlib import Path

from config_ops import filemirror
from config_ops.config import FileTask


def task(source: Path, target: Path, *, overwrite: bool = True) -> FileTask:
    return FileTask("mirror-file", str(source), str(target), overwrite, None)


def write(path: Path, content: str, *, mtime: float) -> None:
    path.write_text(content, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_missing_target_is_created_with_its_folders(tmp_path: Path) -> None:
    source = tmp_path / "AGENTS.md"
    write(source, "rules", mtime=1_000_000)
    target = tmp_path / "claude" / "AGENTS.md"

    run = filemirror.mirror_file(task(source, target))

    assert run.return_code == filemirror.COPIED_EXIT_CODE
    assert target.read_text(encoding="utf-8") == "rules"
    assert target.stat().st_mtime == source.stat().st_mtime


def test_equal_target_is_left_alone_and_a_changed_source_is_copied(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    target = tmp_path / "target.txt"
    write(source, "same", mtime=1_000_000)
    filemirror.mirror_file(task(source, target))

    assert filemirror.mirror_file(task(source, target)).return_code == 0

    write(source, "newer", mtime=2_000_000)
    assert filemirror.mirror_file(task(source, target)).return_code == 1
    assert target.read_text(encoding="utf-8") == "newer"


def test_existing_target_stays_when_overwriting_is_off(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    target = tmp_path / "target.txt"
    write(source, "new", mtime=2_000_000)
    write(target, "old", mtime=1_000_000)

    run = filemirror.mirror_file(task(source, target, overwrite=False))

    assert run.return_code == 0
    assert target.read_text(encoding="utf-8") == "old"


def test_missing_source_is_a_failed_run_not_an_exception(tmp_path: Path) -> None:
    run = filemirror.mirror_file(task(tmp_path / "gone.txt", tmp_path / "target.txt"))

    assert run.return_code == filemirror.ERROR_EXIT_CODE
    assert "source is not a file" in run.lines[0]
