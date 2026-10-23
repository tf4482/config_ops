# Help text and the app's own flow.
from __future__ import annotations

from collections.abc import Callable, Mapping

from .archive import run_archive_set
from .arguments import CommandLine, usage_error, usage_failure
from .config import ArchiveSet, Config, DateSet, FileSet, SshSet
from .configfile import PROJECT_NAME
from .dates import run_date_set
from .files import FilesError, run_file_set
from .output import YELLOW, Printer
from .peripherals import PeripheralsError, run_peripherals
from .smb import SmbError, connect
from .ssh import SshError, run_ssh_set

HELP = f"""{PROJECT_NAME} — run configured Windows automation tasks

Usage:
  {PROJECT_NAME}
  {PROJECT_NAME} smb
  {PROJECT_NAME} files <set>
  {PROJECT_NAME} archive <set>
  {PROJECT_NAME} dates <set>
  {PROJECT_NAME} ssh <set>
  {PROJECT_NAME} peripherals [device ...] [on|off|toggle|suspend|resume]
  {PROJECT_NAME} --help | -h | help

Configuration:
  Uses the first existing file without merging:
  1. config.yml beside the executable
  2. ~/.config/{PROJECT_NAME}/config.yml
  The working directory is not searched.
  With no file, creates a user-only template at location 2 and exits 1.

Behavior:
  Without arguments, lists the configured sets and devices.
  smb connects all configured shares.
  files runs Robocopy mirror, copy, move, and sync tasks of one set.
  archive moves media into YYYY/MM/DD folders by creation date.
  dates sets file times from names or metadata, or writes metadata from file times.
  ssh runs one command over ssh without prompting.
  peripherals triggers device URLs; toggle is the default command.
  Sets with "smb: true" connect the shares first.

Exit codes:
  0    Everything succeeded
  1    Configuration, validation, dependency, or runtime error
  130  Interrupted by the user
"""

# Errors of the functional areas that end a command with exit code 1
DOMAIN_ERRORS = (SmbError, FilesError, SshError, PeripheralsError, OSError)

# This app has no per-run options
OPTIONS: Mapping[str, bool] = {}


# Overview of the configured sets and devices
def execute(printer: Printer, config: Config, options: Mapping[str, str]) -> int:
    overview = (
        ("🔌", "smb", [share.share for share in config.smb.mappings] if config.smb else []),
        ("📦", "files", list(config.files)),
        ("🗃️", "archive", list(config.archive)),
        ("🕒", "dates", list(config.dates)),
        ("🔐", "ssh", list(config.ssh)),
        ("💡", "peripherals", list(config.peripherals.devices) if config.peripherals else []),
    )
    shown = [(icon, name, items) for icon, name, items in overview if items]
    if not shown:
        printer.line("⚠️", "Nothing configured yet", YELLOW)
    for icon, name, items in shown:
        printer.line(icon, f"{name} · {', '.join(items)}")
    return 0


# Connect the shares of the smb section
def _smb(printer: Printer, config: Config, arguments: CommandLine) -> int:
    if arguments.positionals:
        return usage_error(arguments.positionals[0])
    if config.smb is None:
        return printer.fail("Configuration", "smb section is missing")
    return _guarded(printer, "SMB", lambda: _connect(printer, config))


# Trigger devices from the peripherals section
def _peripherals(printer: Printer, config: Config, arguments: CommandLine) -> int:
    if config.peripherals is None:
        return printer.fail("Configuration", "peripherals section is missing")
    settings = config.peripherals
    return _guarded(
        printer, "Peripherals", lambda: run_peripherals(printer, settings, arguments.positionals)
    )


# Command that takes the name of one configured set
def _set_command[T](
    section: str,
    category: str,
    sets: Callable[[Config], Mapping[str, T]],
    run: Callable[[Printer, Config, T], int],
) -> Callable[[Printer, Config, CommandLine], int]:
    # Check the set name, then run it
    def command(printer: Printer, config: Config, arguments: CommandLine) -> int:
        available = sets(config)
        names = ", ".join(sorted(available)) or "none configured"
        if len(arguments.positionals) != 1:
            return usage_failure(f"Expected one {section} set name (available: {names})")
        name = arguments.positionals[0]
        if name not in available:
            return usage_failure(f"Unknown {section} set: {name} (available: {names})")
        item = available[name]
        return _guarded(printer, category, lambda: run(printer, config, item))

    return command


# Shares are connected before a set that asks for them
def _connect(printer: Printer, config: Config) -> int:
    if config.smb is not None:
        connect(printer, config.smb)
    return 0


# Robocopy set, with shares connected first when asked
def _run_files(printer: Printer, config: Config, file_set: FileSet) -> int:
    if file_set.smb:
        _connect(printer, config)
    return run_file_set(printer, file_set, config.smb)


# Archive set, with shares connected first when asked
def _run_archive(printer: Printer, config: Config, archive_set: ArchiveSet) -> int:
    if archive_set.smb:
        _connect(printer, config)
    return run_archive_set(printer, archive_set)


# Date set, with shares connected first when asked
def _run_dates(printer: Printer, config: Config, date_set: DateSet) -> int:
    if date_set.smb:
        _connect(printer, config)
    return run_date_set(printer, date_set)


# SSH set; it never needs shares
def _run_ssh(printer: Printer, config: Config, ssh_set: SshSet) -> int:
    return run_ssh_set(printer, ssh_set)


# Turn a domain error into an error line and exit code 1
def _guarded(printer: Printer, category: str, action: Callable[[], int]) -> int:
    try:
        return action()
    except DOMAIN_ERRORS as error:
        return printer.fail(category, " ".join(str(error).split()))


COMMANDS: Mapping[str, Callable[[Printer, Config, CommandLine], int]] = {
    "smb": _smb,
    "files": _set_command("files", "Files", lambda config: config.files, _run_files),
    "archive": _set_command("archive", "Archive", lambda config: config.archive, _run_archive),
    "dates": _set_command("dates", "Dates", lambda config: config.dates, _run_dates),
    "ssh": _set_command("ssh", "SSH", lambda config: config.ssh, _run_ssh),
    "peripherals": _peripherals,
}
