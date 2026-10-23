# SMB connections through "net use", plus UNC path helpers.
from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from .config import SmbMapping, SmbSettings
from .configfile import launcher_dir
from .output import GREEN, YELLOW, Printer

# Password file when the configuration names none
DEFAULT_PASSWORD_FILE = "~/.config/.smbcredentials"
# Exit codes of "net use"
NET_USE_OK = 0
NET_USE_ALREADY_ASSIGNED = 2


# Connections failed or the password file is unusable
class SmbError(RuntimeError):
    pass


# Connect every mapping; raise after all attempts when any failed
def connect(printer: Printer, smb: SmbSettings, directory: Path | None = None) -> None:
    password = read_password(smb.password_file, directory)
    failed: list[str] = []
    for mapping in smb.mappings:
        label = f"{mapping.drive} → {mapping.share}" if mapping.drive else mapping.share
        return_code = run_net_use(mapping.drive, mapping.share, password, user=smb.user)
        if return_code == NET_USE_OK:
            printer.line("✅", f"{label} · connected", GREEN)
        else:
            reason = (
                "drive in use by another share"
                if return_code == NET_USE_ALREADY_ASSIGNED
                else f"exit code {return_code}"
            )
            printer.line("⚠️", f"{label} · {reason}", YELLOW)
            failed.append(label)
    if failed:
        raise SmbError(f"{len(failed)} connection(s) failed: {', '.join(failed)}")


# Password from the configured file; trailing line breaks are dropped
def read_password(password_file: str | None, directory: Path | None = None) -> str:
    path = password_path(password_file, directory)
    try:
        password = path.read_text(encoding="utf-8-sig").rstrip("\r\n")
    except UnicodeError:
        # The message of a decoding error could echo file content
        raise SmbError(f"password file {path} is not valid UTF-8") from None
    except OSError as error:
        raise SmbError(f"cannot read password file {path}: {error.strerror}") from None
    if not password:
        raise SmbError(f"password file {path} is empty")
    return password


# Default file, an absolute or ~ path as given, anything else beside the executable
def password_path(password_file: str | None, directory: Path | None = None) -> Path:
    path = Path(password_file or DEFAULT_PASSWORD_FILE).expanduser()
    return path if path.is_absolute() else (directory or launcher_dir()) / path


# Open one mapped or deviceless connection and return the "net use" exit code
def run_net_use(drive: str | None, share: str, password: str, *, user: str) -> int:
    # A deviceless share with a live session needs no new connection
    if drive is None and unc_share_accessible(share):
        return NET_USE_OK

    result = subprocess.run(
        build_net_use_command(drive, share, password, user=user),
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != NET_USE_ALREADY_ASSIGNED or drive is None:
        return result.returncode

    # An existing mapping of the same share counts as connected
    existing = _existing_drive_share(drive)
    if existing is not None and _normalize(existing) == _normalize(share):
        return NET_USE_OK
    return result.returncode


# Mapped connections persist, deviceless ones do not
def build_net_use_command(drive: str | None, share: str, password: str, *, user: str) -> list[str]:
    connection = [drive, share] if drive else [share]
    persistence = "yes" if drive else "no"
    return [
        "net",
        "use",
        *connection,
        password,
        f"/USER:{user}",
        "/Y",
        f"/persistent:{persistence}",
    ]


# Whether an existing Windows session can reach the share
def unc_share_accessible(share: str) -> bool:
    try:
        return Path(share).is_dir()
    except OSError:
        return False


# Server and share part of a UNC path; None for other paths
def unc_share_root(path: str) -> str | None:
    if not path.startswith("\\\\"):
        return None
    parts = path.lstrip("\\").split("\\")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return f"\\\\{parts[0]}\\{parts[1]}"


# Whether the mappings cover the share of a UNC path; other paths always pass
def unc_path_covered(path: str, mappings: Sequence[SmbMapping]) -> bool:
    root = unc_share_root(path)
    if root is None:
        return True
    covered = {
        _normalize(mapping_root)
        for mapping in mappings
        if (mapping_root := unc_share_root(mapping.share)) is not None
    }
    return _normalize(root) in covered


# Share currently mapped to a drive, if any
def _existing_drive_share(drive: str) -> str | None:
    result = subprocess.run(
        ["net", "use", drive],
        check=False,
        capture_output=True,
        text=True,
        encoding="oem",
        errors="replace",
    )
    if result.returncode != NET_USE_OK:
        return None
    for part in f"{result.stdout}\n{result.stderr}".split():
        if part.startswith("\\\\"):
            return part
    return None


# Case-insensitive comparison key for UNC paths
def _normalize(path: str) -> str:
    return path.rstrip("\\/").casefold()
