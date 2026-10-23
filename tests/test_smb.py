from __future__ import annotations

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
