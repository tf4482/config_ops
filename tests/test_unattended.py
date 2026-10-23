"""Regression tests for unattended command-line behavior."""

import importlib.util
import io
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

import archive_media
import file_operations
import ssh_tasks


def load_script_module(filename: str, name: str) -> ModuleType:
    """Load a root script with a non-standard Python suffix."""

    path = Path(__file__).resolve().parents[1] / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load test module: {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


peripherals = load_script_module("peripherals.pyw", "peripherals_for_tests")


def test_visual_output_is_preserved_without_tty() -> None:
    output = io.StringIO()

    file_operations.print_visual("remote result", emoji="success", color="success", stream=output)

    assert output.getvalue() == "✅ remote result\n"
    assert "\x1b[" not in output.getvalue()


def test_visual_output_uses_utf8_when_redirected() -> None:
    raw_output = io.BytesIO()
    output = io.TextIOWrapper(raw_output, encoding="cp1252")

    file_operations.print_visual("remote → result", emoji="success", stream=output)

    assert raw_output.getvalue() == "✅ remote → result\n".encode()


def test_non_interactive_menu_requires_set_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    stdin = Mock()
    stdin.isatty.return_value = False
    monkeypatch.setattr(sys, "stdin", stdin)

    with pytest.raises(SystemExit, match="requires a set name.*backup"):
        file_operations.choose_mapping_key_terminal(
            {"backup": {}},
            header="sets",
            empty_message="empty",
        )


def test_non_interactive_smb_password_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    stdin = Mock()
    stdin.isatty.return_value = False
    monkeypatch.setattr(sys, "stdin", stdin)

    with pytest.raises(RuntimeError, match="non-interactive mode"):
        file_operations.prompt_password()


def test_deviceless_smb_connection_uses_unc_share_directly() -> None:
    mappings = file_operations.mappings_from_config(
        {"mappings": [{"share": r"\\server.example\main"}]}
    )

    assert mappings == ((None, r"\\server.example\main"),)
    assert file_operations.build_net_use_command(
        None,
        r"\\server.example\main",
        "secret",
        user=r"DOMAIN\user",
    ) == [
        "net",
        "use",
        r"\\server.example\main",
        "secret",
        r"/USER:DOMAIN\user",
        "/Y",
        "/persistent:no",
    ]


def test_deviceless_smb_reuses_accessible_session(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock()
    monkeypatch.setattr(file_operations, "unc_share_accessible", lambda _share: True)
    monkeypatch.setattr(file_operations.subprocess, "run", run)

    return_code = file_operations.run_net_use(
        None,
        r"\\server.example\main",
        "secret",
        user="user",
    )

    assert return_code == 0
    run.assert_not_called()


def test_mapped_smb_connection_remains_supported() -> None:
    assert file_operations.build_net_use_command(
        "R:",
        r"\\server.example\main",
        "secret",
        user="user",
    ) == [
        "net",
        "use",
        "R:",
        r"\\server.example\main",
        "secret",
        "/USER:user",
        "/Y",
        "/persistent:yes",
    ]


def test_unc_coverage_requires_the_same_server_identity() -> None:
    mappings = ((None, r"\\server.example\main"),)

    assert file_operations.unc_path_covered(r"\\server.example\main\data", mappings)
    assert not file_operations.unc_path_covered(r"\\server\main\data", mappings)


def test_file_operations_reject_unmapped_unc_alias() -> None:
    config_data = {
        "smb": {"mappings": [{"share": r"\\server.example\main"}]},
    }
    operation_set = {
        "smb": True,
        "mirror": [{"source": r"C:\source", "target": r"\\server\main\data"}],
    }

    with pytest.raises(ValueError, match=r"\\\\server\\main.*without a matching SMB mapping"):
        file_operations.validate_operation_set(config_data, operation_set, "backup")


def test_robocopy_accepts_full_unc_path_without_drive_mapping() -> None:
    source = r"\\server.example\main\data\settings\wow_anniversary\WTF"
    command = file_operations.build_robocopy_command(
        source,
        r"C:\backup\WTF",
        ("/E",),
        overwrite=True,
        exclude_dirs=(),
        exclude_files=(),
    )

    assert command == ["robocopy", source, r"C:\backup\WTF", "/E"]


def test_robocopy_oem_output_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    process = Mock(stdout=iter(["Robustes Dateikopieren für Windows\n"]))
    process.wait.return_value = 0
    popen = Mock(return_value=process)
    print_plain = Mock()
    monkeypatch.setattr(file_operations.subprocess, "Popen", popen)
    monkeypatch.setattr(file_operations, "print_plain", print_plain)

    return_code = file_operations.invoke_robocopy(
        r"C:\source",
        r"C:\target",
        ("/E",),
        exclude_dirs=(),
        exclude_files=(),
    )

    assert return_code == 0
    assert popen.call_args.kwargs["encoding"] == "oem"
    print_plain.assert_called_once_with("Robustes Dateikopieren für Windows\n")


def test_strict_boolean_rejects_quoted_false() -> None:
    with pytest.raises(TypeError, match="must be true or false"):
        file_operations.optional_bool({"overwrite": "false"}, "overwrite", label="overwrite", default=True)


def test_normalized_extension_set_strips_whitespace() -> None:
    assert archive_media.normalized_extension_set(
        {"extensions": [" .JPG "]},
        "extensions",
        label="extensions",
    ) == {".jpg"}


def test_archive_rejects_target_below_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(ValueError, match="must not be the source"):
        archive_media.validate_archive_paths(source, source / "archive")


def test_archive_never_deletes_existing_directory(tmp_path: Path) -> None:
    destination = tmp_path / "existing"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(IsADirectoryError):
        archive_media.remove_destination_if_exists(destination)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_ssh_uses_batch_mode_and_inherits_output(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock()
    monkeypatch.setattr(ssh_tasks.subprocess, "run", run)

    ssh_tasks.run_ssh_task("backup", "user", "host", 2222, "backup", 60)

    command = run.call_args.args[0]
    assert command == ["ssh", "-o", "BatchMode=yes", "-p", "2222", "user@host", "backup"]
    assert run.call_args.kwargs == {"check": True, "timeout": 60}


def test_peripheral_state_changes_only_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    device = peripherals.PeripheralDevice("led", "https://on", "https://off")
    trigger = Mock(side_effect=subprocess.CalledProcessError(22, ["curl.exe"]))
    write_state = Mock()
    monkeypatch.setattr(peripherals, "trigger_url", trigger)
    monkeypatch.setattr(peripherals, "write_device_state", write_state)

    with pytest.raises(subprocess.CalledProcessError):
        peripherals.turn_device_on("Software\\peripherals", device)

    write_state.assert_not_called()
