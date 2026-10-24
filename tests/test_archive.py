from __future__ import annotations

import io
from pathlib import Path

import pytest

from config_ops import archive
from config_ops.config import ArchiveSet, PathPair
from config_ops.output import Printer
from config_ops.smb import Unreachable


def test_target_below_source_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not be the source"):
        archive.validate_paths(tmp_path, tmp_path / "archive")


def test_existing_directory_is_never_deleted(tmp_path: Path) -> None:
    destination = tmp_path / "existing"
    destination.mkdir()
    (destination / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(IsADirectoryError):
        archive.remove_destination(destination)

    assert (destination / "keep.txt").exists()


def test_media_is_moved_into_dated_folders(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "photo.JPG").write_text("image", encoding="utf-8")
    (source / "notes.txt").write_text("text", encoding="utf-8")
    pair = PathPair(str(source), str(tmp_path / "out"))
    archive_set = ArchiveSet(False, frozenset({".jpg"}), (pair,))

    assert archive.run_archive_set(Printer(io.StringIO(), color=False), archive_set) == 0

    assert [path.name for path in (tmp_path / "out").rglob("*.JPG")] == ["photo.JPG"]
    assert (source / "notes.txt").exists()


def test_archive_task_on_an_unreachable_share_is_skipped(tmp_path: Path) -> None:
    pair = PathPair(r"\server.example\main\in", str(tmp_path / "out"))
    archive_set = ArchiveSet(False, frozenset({".jpg"}), (pair,))
    unreachable = (Unreachable("vx-00", (r"\server.example\main",)),)
    stream = io.StringIO()

    assert archive.run_archive_set(Printer(stream, color=False), archive_set, unreachable) == 0

    assert "1 skipped" in stream.getvalue()
    assert "Skipped, SMB unreachable: vx-00" in stream.getvalue()
