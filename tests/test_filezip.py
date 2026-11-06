from __future__ import annotations

import zipfile
from pathlib import Path

from config_ops import filezip
from config_ops.config import FileTask


def task(source: Path, target: Path, *, overwrite: bool = True) -> FileTask:
    return FileTask("zip", str(source), str(target), overwrite, None)


def test_folder_is_zipped_into_a_new_target_folder(tmp_path: Path) -> None:
    source = tmp_path / ".ssh"
    (source / "sub").mkdir(parents=True)
    (source / "config").write_text("a", encoding="utf-8")
    (source / "sub" / "key").write_text("b", encoding="utf-8")
    target = tmp_path / "backup" / "ssh"

    run = filezip.zip_folder(task(source, target))

    assert run.return_code == filezip.COPIED_EXIT_CODE
    with zipfile.ZipFile(target / ".ssh.zip") as bundle:
        assert sorted(bundle.namelist()) == ["config", "sub/key"]


def test_existing_archive_is_kept_without_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "data"
    source.mkdir()
    (source / "file.txt").write_text("new", encoding="utf-8")
    target = tmp_path / "out"
    target.mkdir()
    (target / "data.zip").write_text("old", encoding="utf-8")

    kept = filezip.zip_folder(task(source, target, overwrite=False))
    assert kept.return_code == filezip.UNCHANGED_EXIT_CODE
    assert (target / "data.zip").read_text(encoding="utf-8") == "old"

    replaced = filezip.zip_folder(task(source, target))
    assert replaced.return_code == filezip.COPIED_EXIT_CODE
    assert zipfile.is_zipfile(target / "data.zip")


def test_missing_source_fails(tmp_path: Path) -> None:
    run = filezip.zip_folder(task(tmp_path / "gone", tmp_path / "out"))

    assert run.return_code == filezip.ERROR_EXIT_CODE


def test_excluded_files_and_folders_stay_out(tmp_path: Path) -> None:
    source = tmp_path / "data"
    (source / "cache").mkdir(parents=True)
    (source / "cache" / "x").write_text("x", encoding="utf-8")
    (source / "keep.txt").write_text("k", encoding="utf-8")
    (source / "skip.pyc").write_text("s", encoding="utf-8")
    zip_task = FileTask("zip", str(source), str(tmp_path / "out"), True, None, ("cache",), ("*.pyc",))

    filezip.zip_folder(zip_task)

    with zipfile.ZipFile(tmp_path / "out" / "data.zip") as bundle:
        assert bundle.namelist() == ["keep.txt"]
