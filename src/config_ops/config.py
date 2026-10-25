# Embedded default configuration, typed Config, and strict validation.
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .configfile import ConfigError, mapping, port, positive_seconds, text

# Must match config.example.yml
DEFAULT_CONFIG = r"""# SMB shares for sets with "smb: true"; drop the section when unused
smb:
  user: "CHANGE_ME"           # e.g. DOMAIN\user; a username= line in the password file wins
  # Password file: a bare file name is looked up beside the executable, a path is used as given.
  # Plain password, or lines username=... and password=... Default: ~/.config/.smbcredentials
  # password_file: "smb.pass"
  mappings:
    - share: '\\server\main'
    - share: '\\server\backup'
      drive: "R:"

# Settings for all Robocopy tasks (mirror, copy, move, sync); excludes of a task add to these
robocopy:
  # common_options: ["/MT:32", "/W:2", "/R:10", "/XJ", "/XC"]   # default for sets without their own
  exclude_dirs:
    - "cache"
    - "shadercache"
    - ".recycle"
    - "$RECYCLE.BIN"
    - "System Volume Information"
    - "My Music"
    - "My Videos"
    - "My Pictures"
  exclude_files:
    - "desktop.ini"

# Robocopy sets: config_ops files <set>
files:
  backup:
    smb: false
    # common_options: ["/MT:32", "/W:2", "/R:10", "/XJ", "/XC"]
    tasks:
      - type: mirror          # mirror | mirror-file | copy | move | sync
        source: 'C:\data'
        target: 'D:\backup\data'
        exclude_dirs: [".venv", "node_modules"]   # optional, also for copy, move, sync
        exclude_files: ["*.pyc"]                  # optional
      - type: copy
        source: 'C:\documents'
        target: 'D:\backup\documents'
        overwrite: false      # optional, default true
      - type: mirror-file     # one file; copied when missing or different
        source: 'C:\settings\app.ini'
        target:               # one path, or a list for mirror and mirror-file
          - 'D:\backup\app.ini'
          - 'E:\backup\app.ini'

# Media archive sets: config_ops archive <set>
archive:
  photos:
    smb: false
    extensions: [".jpg", ".png", ".mp4"]
    tasks:
      - source: 'C:\import'
        target: 'D:\photos'

# File date sets: config_ops dates <set>
dates:
  screenshots:
    smb: false
    mode: file                # file | folder | metadata | metadata_reverse
    source_folder: 'C:\screenshots'
    extensions: [".jpg", ".png"]
    patterns:                 # needed for file and folder mode
      - '^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})'
    # target_folder: 'C:\screenshots-adjusted'
    # change_files_in_place: true
    # overwrite: false
    # hour_adjustment: 0

# SSH command sets: config_ops ssh <set>
ssh:
  remote_backup:
    user: "backup"
    host: "192.0.2.10"
    port: 22
    timeout_seconds: 300
    command: "ls"
"""

FileKind = Literal["mirror", "mirror-file", "copy", "move", "sync"]
DateMode = Literal["file", "folder", "metadata", "metadata_reverse"]

FILE_KINDS: tuple[FileKind, ...] = ("mirror", "mirror-file", "copy", "move", "sync")
MULTI_TARGET_KINDS = ("mirror", "mirror-file")
DATE_MODES: tuple[DateMode, ...] = ("file", "folder", "metadata", "metadata_reverse")
TIMESTAMP_GROUPS = {"month", "day"}


# One SMB share with an optional drive letter
@dataclass(frozen=True, slots=True)
class SmbMapping:
    share: str
    drive: str | None


# Shared SMB account and shares
@dataclass(frozen=True, slots=True)
class SmbSettings:
    user: str | None
    password_file: str | None
    mappings: tuple[SmbMapping, ...]


# One Robocopy operation; its excludes add to the global ones
@dataclass(frozen=True, slots=True)
class FileTask:
    kind: FileKind
    source: str
    target: str
    overwrite: bool
    options: tuple[str, ...] | None
    exclude_dirs: tuple[str, ...] = ()
    exclude_files: tuple[str, ...] = ()


# Settings for all Robocopy tasks
@dataclass(frozen=True, slots=True)
class RobocopySettings:
    common_options: tuple[str, ...] | None = None
    exclude_dirs: tuple[str, ...] = ()
    exclude_files: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FileSet:
    smb: bool
    common_options: tuple[str, ...] | None
    tasks: tuple[FileTask, ...]


@dataclass(frozen=True, slots=True)
class PathPair:
    source: str
    target: str


@dataclass(frozen=True, slots=True)
class ArchiveSet:
    smb: bool
    extensions: frozenset[str]
    tasks: tuple[PathPair, ...]


@dataclass(frozen=True, slots=True)
class DateSet:
    smb: bool
    mode: DateMode
    source_folder: str
    target_folder: str | None
    extensions: frozenset[str]
    patterns: tuple[re.Pattern[str], ...]
    change_files_in_place: bool
    overwrite: bool
    hour_adjustment: int


@dataclass(frozen=True, slots=True)
class SshSet:
    user: str
    host: str
    port: int
    timeout_seconds: float
    command: str


# Validated configuration; every section is optional
@dataclass(frozen=True, slots=True)
class Config:
    smb: SmbSettings | None
    robocopy: RobocopySettings
    files: Mapping[str, FileSet]
    archive: Mapping[str, ArchiveSet]
    dates: Mapping[str, DateSet]
    ssh: Mapping[str, SshSet]


# Strict validation of the decoded YAML document
def parse_config(document: Any) -> Config:
    root = mapping(document, "configuration")
    _keys(root, set(), {"smb", "robocopy", "files", "archive", "dates", "ssh"}, "configuration")
    config = Config(
        smb=_parse_smb(root["smb"]) if "smb" in root else None,
        robocopy=_parse_robocopy(root["robocopy"]) if "robocopy" in root else RobocopySettings(),
        files=_sets(root.get("files"), "files", _parse_file_set),
        archive=_sets(root.get("archive"), "archive", _parse_archive_set),
        dates=_sets(root.get("dates"), "dates", _parse_date_set),
        ssh=_sets(root.get("ssh"), "ssh", _parse_ssh_set),
    )
    if config.smb is None:
        for section, sets in (
            ("files", config.files),
            ("archive", config.archive),
            ("dates", config.dates),
        ):
            for name, item in sets.items():
                if item.smb:
                    raise ConfigError(f"{section}.{name}.smb requires the smb section")
    return config


# Missing and unknown keys, listed sorted like the frame's exact_keys
def _keys(values: Mapping[str, Any], required: set[str], optional: set[str], location: str) -> None:
    details = []
    if missing := required - values.keys():
        details.append(f"missing: {', '.join(sorted(missing))}")
    if unknown := values.keys() - required - optional:
        details.append(f"unknown: {', '.join(sorted(map(str, unknown)))}")
    if details:
        raise ConfigError(f"{location} keys are invalid ({'; '.join(details)})")


# Named sets of one section; a missing section means no sets
def _sets[T](
    value: Any, location: str, parse: Callable[[Mapping[str, Any], str], T]
) -> dict[str, T]:
    if value is None:
        return {}
    sets = mapping(value, location)
    for name in sets:
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{location} set names must be non-empty strings")
    return {
        name: parse(mapping(item, f"{location}.{name}"), f"{location}.{name}")
        for name, item in sets.items()
    }


# Strict boolean with a default for a missing key
def _flag(values: Mapping[str, Any], key: str, location: str, *, default: bool) -> bool:
    value = values.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{location}.{key} must be true or false")
    return value


# Strict integer with a default for a missing key
def _integer(values: Mapping[str, Any], key: str, location: str, *, default: int) -> int:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{location}.{key} must be an integer")
    return value


# Non-empty list of trimmed non-empty strings
def _text_list(values: Mapping[str, Any], key: str, location: str) -> tuple[str, ...]:
    value = values.get(key)
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{location}.{key} must be a non-empty list of strings")
    items = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{location}.{key}[{index}] must be a non-empty string")
        items.append(item.strip())
    return tuple(items)


# Non-empty list of strings when the key is present, else None
def _optional_list(values: Mapping[str, Any], key: str, location: str) -> tuple[str, ...] | None:
    return _text_list(values, key, location) if key in values else None


# Lowercase file extensions with a leading dot
def _extensions(values: Mapping[str, Any], location: str) -> frozenset[str]:
    extensions = frozenset(item.lower() for item in _text_list(values, "extensions", location))
    for extension in sorted(extensions):
        if not extension.startswith(".") or len(extension) < 2:
            raise ConfigError(f"{location}.extensions must contain extensions like .jpg")
    return extensions


# Validate the smb section
def _parse_smb(value: Any) -> SmbSettings:
    smb = mapping(value, "smb")
    _keys(smb, {"mappings"}, {"user", "password_file"}, "smb")
    entries = smb["mappings"]
    if not isinstance(entries, list) or not entries:
        raise ConfigError("smb.mappings must be a non-empty list")
    mappings = []
    for index, entry in enumerate(entries):
        location = f"smb.mappings[{index}]"
        item = mapping(entry, location)
        _keys(item, {"share"}, {"drive"}, location)
        drive = text(item, "drive", location) if "drive" in item else None
        mappings.append(SmbMapping(share=text(item, "share", location), drive=drive))
    return SmbSettings(
        user=text(smb, "user", "smb") if "user" in smb else None,
        password_file=text(smb, "password_file", "smb") if "password_file" in smb else None,
        mappings=tuple(mappings),
    )


# Validate the robocopy section
def _parse_robocopy(value: Any) -> RobocopySettings:
    robocopy = mapping(value, "robocopy")
    _keys(robocopy, set(), {"common_options", "exclude_dirs", "exclude_files"}, "robocopy")
    return RobocopySettings(
        common_options=_optional_list(robocopy, "common_options", "robocopy"),
        exclude_dirs=_optional_list(robocopy, "exclude_dirs", "robocopy") or (),
        exclude_files=_optional_list(robocopy, "exclude_files", "robocopy") or (),
    )


# Validate one files set
def _parse_file_set(values: Mapping[str, Any], location: str) -> FileSet:
    _keys(values, {"tasks"}, {"smb", "common_options"}, location)
    common = _text_list(values, "common_options", location) if "common_options" in values else None
    entries = values["tasks"]
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{location}.tasks must be a non-empty list")
    tasks = []
    for index, entry in enumerate(entries):
        task_location = f"{location}.tasks[{index}]"
        item = mapping(entry, task_location)
        _keys(
            item,
            {"type", "source", "target"},
            {"overwrite", "options", "exclude_dirs", "exclude_files"},
            task_location,
        )
        kind = text(item, "type", task_location)
        if kind not in FILE_KINDS:
            raise ConfigError(f"{task_location}.type must be one of: {', '.join(FILE_KINDS)}")
        options = _text_list(item, "options", task_location) if "options" in item else None
        exclude_dirs = _optional_list(item, "exclude_dirs", task_location)
        exclude_files = _optional_list(item, "exclude_files", task_location)
        if kind == "mirror-file" and (options or exclude_dirs or exclude_files):
            raise ConfigError(f"{task_location} options and excludes are not used by mirror-file")
        source = text(item, "source", task_location)
        overwrite = _flag(item, "overwrite", task_location, default=True)
        # One task per target, run and reported on its own
        for target in _targets(item, kind, task_location):
            tasks.append(
                FileTask(
                    kind=kind,  # pyright: ignore[reportArgumentType]
                    source=source,
                    target=target,
                    overwrite=overwrite,
                    options=options,
                    exclude_dirs=exclude_dirs or (),
                    exclude_files=exclude_files or (),
                )
            )
    return FileSet(_flag(values, "smb", location, default=False), common, tuple(tasks))


# One target, or a list of targets for mirror and mirror-file
def _targets(item: Mapping[str, Any], kind: str, location: str) -> tuple[str, ...]:
    if not isinstance(item.get("target"), list):
        return (text(item, "target", location),)
    if kind not in MULTI_TARGET_KINDS:
        raise ConfigError(f"{location}.target must be a single path for {kind}")
    return _text_list(item, "target", location)


# Validate one archive set
def _parse_archive_set(values: Mapping[str, Any], location: str) -> ArchiveSet:
    _keys(values, {"extensions", "tasks"}, {"smb"}, location)
    entries = values["tasks"]
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{location}.tasks must be a non-empty list")
    tasks = []
    for index, entry in enumerate(entries):
        task_location = f"{location}.tasks[{index}]"
        item = mapping(entry, task_location)
        _keys(item, {"source", "target"}, set(), task_location)
        tasks.append(
            PathPair(text(item, "source", task_location), text(item, "target", task_location))
        )
    return ArchiveSet(
        smb=_flag(values, "smb", location, default=False),
        extensions=_extensions(values, location),
        tasks=tuple(tasks),
    )


# Validate one dates set
def _parse_date_set(values: Mapping[str, Any], location: str) -> DateSet:
    optional = {
        "smb",
        "patterns",
        "target_folder",
        "change_files_in_place",
        "overwrite",
        "hour_adjustment",
    }
    _keys(values, {"mode", "source_folder", "extensions"}, optional, location)
    mode = text(values, "mode", location)
    if mode not in DATE_MODES:
        raise ConfigError(f"{location}.mode must be one of: {', '.join(DATE_MODES)}")
    uses_patterns = mode in ("file", "folder")
    if uses_patterns:
        patterns = tuple(
            _compile(pattern, f"{location}.patterns")
            for pattern in _text_list(values, "patterns", location)
        )
    elif "patterns" in values:
        raise ConfigError(f"{location}.patterns is only used in file and folder mode")
    else:
        patterns = ()
    return DateSet(
        smb=_flag(values, "smb", location, default=False),
        mode=mode,  # pyright: ignore[reportArgumentType]
        source_folder=text(values, "source_folder", location),
        target_folder=text(values, "target_folder", location)
        if "target_folder" in values
        else None,
        extensions=_extensions(values, location),
        patterns=patterns,
        change_files_in_place=_flag(values, "change_files_in_place", location, default=True),
        overwrite=_flag(values, "overwrite", location, default=False),
        hour_adjustment=_integer(values, "hour_adjustment", location, default=0),
    )


# Regex with the named groups the timestamp parser needs
def _compile(pattern: str, location: str) -> re.Pattern[str]:
    try:
        compiled = re.compile(pattern, flags=re.IGNORECASE)
    except re.error as error:
        raise ConfigError(f"{location} has an invalid regex: {error}") from None
    groups = set(compiled.groupindex)
    if not ({"year", "year2"} & groups) or not groups >= TIMESTAMP_GROUPS:
        raise ConfigError(f"{location} needs named groups year or year2, month, and day")
    return compiled


# Validate one ssh set
def _parse_ssh_set(values: Mapping[str, Any], location: str) -> SshSet:
    _keys(values, {"user", "host", "port", "timeout_seconds", "command"}, set(), location)
    return SshSet(
        user=text(values, "user", location),
        host=text(values, "host", location),
        port=port(values, "port", location),
        timeout_seconds=positive_seconds(values, "timeout_seconds", location),
        command=text(values, "command", location),
    )
