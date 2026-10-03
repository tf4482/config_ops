"""Sync directory and unique-file behavior."""

import pytest

import file_operations
from winutils_python import file_ops


@pytest.mark.parametrize("existing_side", ("source", "target"))
def test_sync_creates_missing_root_and_copies_unique_file(tmp_path, existing_side):
    """Missing root creation and unique-file propagation."""

    source = tmp_path / "source"
    target = tmp_path / "target"
    existing = source if existing_side == "source" else target
    existing.mkdir()
    (existing / "only-here.txt").write_text("content", encoding="utf-8")
    (existing / "nested").mkdir()
    (existing / "nested" / "nested-only.txt").write_text("nested", encoding="utf-8")

    file_operations.prepare_sync_directories(
        {"sync": [{"source": str(source), "target": str(target)}]}
    )

    assert source.is_dir() and target.is_dir()
    assert file_ops.sync(str(source), str(target)) < file_ops.ROBOCOPY_FAILURE_EXIT_CODE
    assert (source / "only-here.txt").read_text(encoding="utf-8") == "content"
    assert (target / "only-here.txt").read_text(encoding="utf-8") == "content"
    assert (source / "nested" / "nested-only.txt").read_text(encoding="utf-8") == "nested"
    assert (target / "nested" / "nested-only.txt").read_text(encoding="utf-8") == "nested"


def test_sync_rejects_nested_roots_before_creating_them(tmp_path):
    """Nested root rejection without directory creation."""

    source = tmp_path / "source"
    target = source / "nested"

    with pytest.raises(ValueError, match="must not contain one another"):
        file_operations.prepare_sync_directories(
            {"sync": [{"source": str(source), "target": str(target)}]}
        )

    assert not source.exists()
