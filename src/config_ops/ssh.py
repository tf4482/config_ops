# Non-interactive SSH commands.
from __future__ import annotations

import subprocess

from .config import SshSet
from .output import GREEN, Printer


# ssh missing, timed out, or exited with an error
class SshError(RuntimeError):
    pass


# Run the command of a set; remote output goes straight to the terminal
def run_ssh_set(printer: Printer, ssh_set: SshSet) -> int:
    printer.line("🔐", f"{ssh_set.user}@{ssh_set.host}:{ssh_set.port}")
    run_ssh_task(ssh_set)
    printer.line("✅", "command finished", GREEN)
    return 0


# BatchMode makes ssh fail instead of prompting for input
def run_ssh_task(ssh_set: SshSet) -> None:
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-p",
        str(ssh_set.port),
        f"{ssh_set.user}@{ssh_set.host}",
        ssh_set.command,
    ]
    try:
        subprocess.run(command, check=True, timeout=ssh_set.timeout_seconds)
    except FileNotFoundError:
        raise SshError("missing local dependency: ssh") from None
    except subprocess.TimeoutExpired:
        raise SshError(f"timeout after {ssh_set.timeout_seconds:g} seconds") from None
    except subprocess.CalledProcessError as error:
        raise SshError(f"ssh exited with code {error.returncode}") from None
