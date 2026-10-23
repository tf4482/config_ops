from __future__ import annotations

from unittest.mock import Mock

import pytest

from config_ops import ssh
from config_ops.config import SshSet


def test_ssh_uses_batch_mode_and_inherits_output(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock()
    monkeypatch.setattr(ssh.subprocess, "run", run)

    ssh.run_ssh_task(SshSet("user", "host.example", 2222, 60, "backup"))

    assert run.call_args.args[0] == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-p",
        "2222",
        "user@host.example",
        "backup",
    ]
    assert run.call_args.kwargs == {"check": True, "timeout": 60}
