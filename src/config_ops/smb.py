# SMB connections through "net use", plus UNC path helpers.
from __future__ import annotations

import re
import subprocess
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import SmbMapping, SmbSettings
from .configfile import launcher_dir
from .output import GREEN, YELLOW, Printer

# Password file when the configuration names none
DEFAULT_PASSWORD_FILE = "~/.config/.smbcredentials"
# Exit codes of "net use"
NET_USE_OK = 0
NET_USE_ALREADY_ASSIGNED = 2
# "System error 53 has occurred" and its translations
SYSTEM_ERROR = re.compile(r"(?i)(?:error|fehler)\s+(\d+)")
# System errors that mean the server or network cannot be reached, not that access is refused
UNREACHABLE_ERRORS = frozenset({51, 53, 64, 67, 121, 1203, 1222, 1225, 1231})


# Connections failed or the password file is unusable
class SmbError(RuntimeError):
    pass


# Exit code of "net use" and its message without secrets
@dataclass(frozen=True, slots=True)
class NetUseResult:
    return_code: int
    detail: str = ""

    # System error number named in the message of "net use"
    @property
    def system_error(self) -> int | None:
        match = SYSTEM_ERROR.search(self.detail)
        return int(match.group(1)) if match else None


# A share that cannot be reached: its display label and the path prefixes it covers
@dataclass(frozen=True, slots=True)
class Unreachable:
    label: str
    prefixes: tuple[str, ...]


# User and password from the credentials file
@dataclass(frozen=True, slots=True)
class Credentials:
    user: str | None
    password: str


# Connect every mapping; unreachable shares are returned, other failures raise at the end
def connect(
    printer: Printer, smb: SmbSettings, directory: Path | None = None
) -> tuple[Unreachable, ...]:
    credentials = read_credentials(smb.password_file, directory)
    # A username in the file wins over smb.user of the configuration
    user = credentials.user or smb.user
    if user is None:
        raise SmbError("no user: set smb.user or a username= line in the password file")
    failed: list[str] = []
    unreachable: list[Unreachable] = []
    for mapping in smb.mappings:
        label = f"{mapping.drive} → {mapping.share}" if mapping.drive else mapping.share
        result = run_net_use(mapping.drive, mapping.share, credentials.password, user=user)
        if result.return_code == NET_USE_OK:
            printer.line("✅", f"{label} · connected", GREEN)
        elif result.system_error in UNREACHABLE_ERRORS:
            printer.line("⚠️", f"{label} · unreachable · {result.detail}", YELLOW)
            unreachable.append(_unreachable(mapping, label))
        else:
            printer.line("⚠️", f"{label} · {_reason(mapping, result)}", YELLOW)
            failed.append(label)
    if failed:
        source = password_path(smb.password_file, directory)
        raise SmbError(
            f"{len(failed)} connection(s) failed: {', '.join(failed)}; "
            f"check smb.user and the password file {source}"
        )
    return tuple(unreachable)


# The unreachable share that holds one of the paths, if any
def blocked_by(unreachable: Sequence[Unreachable], *paths: str) -> Unreachable | None:
    for share in unreachable:
        for path in paths:
            if any(_under(path, prefix) for prefix in share.prefixes):
                return share
    return None


# Closing note about work that was skipped; skipped is one share label per skipped item
def report_skipped(printer: Printer, skipped: Sequence[str]) -> None:
    for label, count in Counter(skipped).items():
        printer.line("⚠️", f"Skipped, SMB unreachable: {label} · {count} item(s)", YELLOW)


# Credentials from the configured file: a plain password, or username= and password= lines
def read_credentials(password_file: str | None, directory: Path | None = None) -> Credentials:
    path = password_path(password_file, directory)
    try:
        credentials = _parse_credentials(path.read_text(encoding="utf-8-sig"))
    except UnicodeError:
        # The message of a decoding error could echo file content
        raise SmbError(f"password file {path} is not valid UTF-8") from None
    except OSError as error:
        hint = "" if password_file else " (default file, set smb.password_file to change it)"
        raise SmbError(f"cannot read password file {path}: {error.strerror}{hint}") from None
    if not credentials.password:
        raise SmbError(f"password file {path} is empty")
    return credentials


# Default file, an absolute or ~ path as given, anything else beside the executable
def password_path(password_file: str | None, directory: Path | None = None) -> Path:
    path = Path(password_file or DEFAULT_PASSWORD_FILE).expanduser()
    return path if path.is_absolute() else (directory or launcher_dir()) / path


# Open one mapped or deviceless connection
def run_net_use(drive: str | None, share: str, password: str, *, user: str) -> NetUseResult:
    # A deviceless share with a live session needs no new connection
    if drive is None and unc_share_accessible(share):
        return NetUseResult(NET_USE_OK)

    result = subprocess.run(
        build_net_use_command(drive, share, password, user=user),
        check=False,
        capture_output=True,
        text=True,
        encoding="oem",
        errors="replace",
    )
    # "net use" never echoes the password, but the message is cleaned anyway
    detail = " ".join(f"{result.stdout} {result.stderr}".replace(password, "***").split())
    outcome = NetUseResult(result.returncode, detail)
    if result.returncode != NET_USE_ALREADY_ASSIGNED or drive is None:
        return outcome

    # An existing mapping of the same share counts as connected
    existing = _existing_drive_share(drive)
    if existing is not None and _normalize(existing) == _normalize(share):
        return NetUseResult(NET_USE_OK)
    return outcome


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


# username= and password= lines of a credentials file, else the plain password
def _parse_credentials(content: str) -> Credentials:
    values: dict[str, str] = {}
    for line in content.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip().lower() in ("username", "user", "password"):
            values.setdefault(key.strip().lower(), value)
    if "password" not in values:
        return Credentials(None, content.rstrip("\r\n"))
    user = (values.get("username") or values.get("user") or "").strip()
    return Credentials(user or None, values["password"])


# Why a connection failed; the message of "net use" says more than its exit code
def _reason(mapping: SmbMapping, result: NetUseResult) -> str:
    if mapping.drive and result.return_code == NET_USE_ALREADY_ASSIGNED:
        return "drive in use by another share"
    return f"exit code {result.return_code}" + (f" · {result.detail}" if result.detail else "")


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


# Prefixes of a mapping in comparison form: the share and its drive letter
def _unreachable(mapping: SmbMapping, label: str) -> Unreachable:
    prefixes = [_normalize(unc_share_root(mapping.share) or mapping.share)]
    if mapping.drive:
        prefixes.append(_normalize(mapping.drive))
    return Unreachable(label, tuple(prefixes))


# Whether the path equals the prefix or lies below it
def _under(path: str, prefix: str) -> bool:
    normalized = _normalize(path)
    return normalized == prefix or normalized.startswith((prefix + "\\", prefix + "/"))


# Case-insensitive comparison key for UNC paths
def _normalize(path: str) -> str:
    return path.rstrip("\\/").casefold()
