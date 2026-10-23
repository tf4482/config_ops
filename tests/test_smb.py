from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from config_ops import smb
from config_ops.config import SmbMapping

SHARE = r"\\server.example\main"


def test_deviceless_connection_uses_the_unc_share_directly() -> None:
    assert smb.build_net_use_command(None, SHARE, "secret", user=r"DOMAIN\user") == [
        "net",
        "use",
        SHARE,
        "secret",
        r"/USER:DOMAIN\user",
        "/Y",
        "/persistent:no",
    ]


def test_mapped_connection_is_persistent() -> None:
    command = smb.build_net_use_command("R:", SHARE, "secret", user="user")

    assert command[2:4] == ["R:", SHARE]
    assert command[-1] == "/persistent:yes"


def test_accessible_share_reuses_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock()
    monkeypatch.setattr(smb, "unc_share_accessible", lambda _share: True)
    monkeypatch.setattr(smb.subprocess, "run", run)

    assert smb.run_net_use(None, SHARE, "secret", user="user") == 0
    run.assert_not_called()


def test_unc_coverage_requires_the_same_server_identity() -> None:
    mappings = (SmbMapping(SHARE, None),)

    assert smb.unc_path_covered(rf"{SHARE}\data", mappings)
    assert smb.unc_path_covered(r"C:\local", mappings)
    assert not smb.unc_path_covered(r"\\server\main\data", mappings)


def test_password_is_read_from_the_file_beside_the_executable(tmp_path: Path) -> None:
    (tmp_path / "smb.pass").write_bytes(b"\xef\xbb\xbfs3cret pass\r\n")

    assert smb.read_password("smb.pass", tmp_path) == "s3cret pass"


@pytest.mark.parametrize("content", [b"", b"\r\n", b"\xff\xfesecret"])
def test_unusable_password_files_are_rejected_without_content(
    tmp_path: Path, content: bytes
) -> None:
    (tmp_path / "smb.pass").write_bytes(content)

    with pytest.raises(smb.SmbError) as caught:
        smb.read_password("smb.pass", tmp_path)

    assert "secret" not in str(caught.value)


def test_missing_password_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(smb.SmbError, match="cannot read password file"):
        smb.read_password("missing.pass", tmp_path)


def test_password_file_defaults_to_the_user_config_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USERPROFILE", "/home/example")
    monkeypatch.setenv("HOME", "/home/example")

    expected = Path("/home/example/.config/.smbcredentials")
    assert smb.password_path(None, Path("bin")) == expected.expanduser()


def test_password_file_name_is_looked_up_beside_the_executable(tmp_path: Path) -> None:
    absolute = tmp_path / "elsewhere" / "smb.pass"

    assert smb.password_path("smb.pass", tmp_path / "bin") == tmp_path / "bin" / "smb.pass"
    assert smb.password_path(str(absolute), tmp_path / "bin") == absolute
