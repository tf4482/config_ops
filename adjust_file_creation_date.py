"""Adjust Windows file timestamps from date/time values parsed from filenames.

The script reads named configuration sets from ``config.yaml``, optionally connects
SMB mappings, copies files when requested, and applies parsed timestamps as
creation, access, and modification times through the Win32 ``SetFileTime`` API.
"""

import re
import shutil
import sys
from ctypes import Structure, WinDLL, byref, c_bool, c_uint32, c_void_p, c_wchar_p
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from winutils_python import connect_smb, menu, visual


DEFAULT_SECTION = r'''adjust_file_creation_date:
  example_set:
    smb: false
    source_folder: 'R:\path\to\files'
    target_folder: 'C:\path\to\target'
    extensions:
      - .jpg
      - .jpeg
      - .png
      - .gif
      - .bmp
      - .tif
      - .tiff
    change_files_in_place: true
    overwrite: false
    hour_adjustment: 0
    patterns:
      - pattern: '^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})(?:[ _-]+(?P<hour>\d{2})_(?P<minute>\d{2})_(?P<second>\d{2}))?'
'''

INVALID_HANDLE_VALUE = c_void_p(-1).value
WINDOWS_TICK = 10_000_000
WINDOWS_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
CONFIG_SECTION = "adjust_file_creation_date"
DEFAULT_SMB_REGISTRY_PATH = "Software\\peripherals"
CANCEL_CHOICES = {"exit", "quit", "cancel"}
GENERIC_READ_ATTRIBUTES = 0x0100
FILE_SHARE_READ_WRITE_DELETE = 0x00000001 | 0x00000002 | 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000

kernel32 = WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.argtypes = (
    c_wchar_p,
    c_uint32,
    c_uint32,
    c_void_p,
    c_uint32,
    c_uint32,
    c_void_p,
)
kernel32.CreateFileW.restype = c_void_p
kernel32.SetFileTime.argtypes = (c_void_p, c_void_p, c_void_p, c_void_p)
kernel32.SetFileTime.restype = c_bool
kernel32.CloseHandle.argtypes = (c_void_p,)
kernel32.CloseHandle.restype = c_bool


@dataclass(frozen=True)
class FileAdjustmentResult:
    """Result for a single source file processed by the adjustment workflow."""

    source: Path
    destination: Path | None = None
    timestamp: datetime | None = None
    changed: bool = False
    error: Exception | None = None

    @property
    def failed(self) -> bool:
        """Return whether this file failed during processing."""

        return self.error is not None


class FileCreationDateAdjustmentError(RuntimeError):
    """Raised after processing when one or more file adjustments failed."""

    def __init__(self, results: list[FileAdjustmentResult]) -> None:
        """Build a summary error from failed adjustment results."""

        self.results = results
        failed_results = [result for result in results if result.failed]
        summary = ", ".join(f"{result.source}: {result.error}" for result in failed_results)
        super().__init__(f"{len(failed_results)} file creation date adjustment(s) failed: {summary}")


def app_dir(script_file: str | Path) -> Path:
    """Return the directory containing the script or frozen executable."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(script_file).resolve().parent


def script_dir(script_file: str | Path) -> Path:
    """Return the directory used for script-local runtime files."""

    return app_dir(script_file)


def config_path(script_file: str | Path) -> Path:
    """Return the expected ``config.yaml`` path for this script."""

    return script_dir(script_file) / "config.yaml"


def find_config_path(script_file: str | Path) -> Path:
    """Return the config path, creating an empty config file when missing."""

    path = config_path(script_file)

    if not path.exists():
        path.write_text("", encoding="utf-8")

    return path


def parse_yaml(config_text: str) -> dict[str, Any]:
    """Parse YAML config text into a dictionary, treating empty files as empty."""

    return yaml.safe_load(config_text) or {}


def dump_yaml(config: dict[str, Any]) -> str:
    """Serialize config while omitting internal helper keys."""

    clean = {key: value for key, value in config.items() if not key.startswith("__")}
    return yaml.safe_dump(clean, sort_keys=False, allow_unicode=True)


def load_config(script_file: str | Path) -> dict[str, Any]:
    """Load ``config.yaml`` and attach its path under an internal helper key."""

    path = find_config_path(script_file)
    loaded_config = parse_yaml(path.read_text(encoding="utf-8"))
    loaded_config["__config_path__"] = path
    return loaded_config


def append_section_yaml(config: dict[str, Any], section_yaml: str) -> None:
    """Append a default YAML section to the loaded config file."""

    path = config.get("__config_path__")

    if not isinstance(path, Path):
        return

    existing = path.read_text(encoding="utf-8").rstrip()
    separator = "\n\n" if existing else ""
    path.write_text(existing + separator + section_yaml.strip() + "\n", encoding="utf-8")


def get_table(config: dict[str, Any], name: str) -> dict[str, Any]:
    """Return a top-level config table or raise when the value is not a table."""

    value = config.get(name, {})

    if not isinstance(value, dict):
        raise TypeError(f"Configuration value '{name}' must be a table")

    return value


def ensure_section(config: dict[str, Any]) -> None:
    """Ensure the adjustment section exists and has table shape."""

    if CONFIG_SECTION not in config:
        append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning(
            f"Added default '{CONFIG_SECTION}' section to config.yaml. "
            "Please configure it before running."
        )
        raise SystemExit(1)

    get_adjustment_sets(config)


def config_key(set_name: str, name: str) -> str:
    """Return a human-readable dotted config key for error messages."""

    return f"{CONFIG_SECTION}.{set_name}.{name}"


def get_adjustment_sets(config: dict[str, Any]) -> dict[str, Any]:
    """Return all configured file creation date adjustment sets."""

    return get_table(config, CONFIG_SECTION)


def validate_adjustment_set_name(config: dict[str, Any], set_name: str) -> str:
    """Validate a requested adjustment set name and return it unchanged."""

    adjustment_sets = get_adjustment_sets(config)

    if set_name not in adjustment_sets:
        available_sets = ", ".join(adjustment_sets) or "none"
        raise SystemExit(
            f"Unknown file creation date adjustment set '{set_name}'. "
            f"Available sets: {available_sets}"
        )

    return set_name


def adjustment_set_config(config: dict[str, Any], set_name: str) -> dict[str, Any]:
    """Return the configuration table for a named adjustment set."""

    adjustment_sets = get_adjustment_sets(config)
    adjustment_set = adjustment_sets.get(set_name)

    if not isinstance(adjustment_set, dict):
        raise TypeError(f"File creation date adjustment set '{set_name}' must be a table")

    return adjustment_set


def choose_adjustment_set_terminal(config: dict[str, Any]) -> str:
    """Prompt the user to choose an adjustment set from the terminal."""

    adjustment_sets = get_adjustment_sets(config)
    return menu.choose_mapping_key_terminal(
        adjustment_sets,
        header="Available file creation date adjustment sets:",
        empty_message=(
            "No file creation date adjustment sets configured in config.yaml. "
            f"Please add an '{CONFIG_SECTION}' section."
        ),
    )


def adjustment_set_name(config: dict[str, Any]) -> str:
    """Return the selected adjustment set from CLI args or the terminal menu."""

    if len(sys.argv) > 1:
        return validate_adjustment_set_name(config, menu.normalize_selection_name(sys.argv[1]))

    return choose_adjustment_set_terminal(config)


def registry_path_from_config(script_config: dict[str, Any], set_name: str) -> str:
    """Return the registry path used by related SMB/peripheral integrations."""

    registry_path = str(script_config.get("smb_registry_path", DEFAULT_SMB_REGISTRY_PATH)).strip()

    if not registry_path:
        raise ValueError(f"Configuration value '{config_key(set_name, 'smb_registry_path')}' must be set")

    return registry_path


def source_folder_from_config(script_config: dict[str, Any]) -> Path:
    """Return the source folder configured for the adjustment set."""

    return Path(str(script_config["source_folder"]))


def change_files_in_place_from_config(script_config: dict[str, Any]) -> bool:
    """Return whether matching files should be modified in place."""

    return bool(script_config.get("change_files_in_place", True))


def overwrite_from_config(script_config: dict[str, Any]) -> bool:
    """Return whether copied target files may be overwritten."""

    return bool(script_config.get("overwrite", False))


def target_folder_from_config(
    script_config: dict[str, Any],
    source_folder: Path,
    change_files_in_place: bool,
) -> Path:
    """Return the destination folder for copied files."""

    if change_files_in_place:
        return source_folder

    target_folder = script_config.get("target_folder")

    if target_folder:
        return Path(str(target_folder))

    return source_folder / "changed_date"


def hour_adjustment_from_config(script_config: dict[str, Any]) -> int:
    """Return the configured hour offset applied to parsed timestamps."""

    return int(script_config.get("hour_adjustment", 0))


def read_config_list(script_config: dict[str, Any], set_name: str, name: str) -> list[Any]:
    """Read and validate a required non-empty list from a set config."""

    value = script_config.get(name, [])

    if not isinstance(value, list):
        raise TypeError(f"Configuration value '{config_key(set_name, name)}' must be a list")

    if not value:
        raise ValueError(f"Configuration value '{config_key(set_name, name)}' must define at least one entry")

    return value


def get_file_extensions(script_config: dict[str, Any], set_name: str) -> set[str]:
    """Return normalized lowercase file extensions to process."""

    extensions = read_config_list(script_config, set_name, "extensions")
    normalized_extensions = {str(extension).lower() for extension in extensions if str(extension).strip()}

    if not normalized_extensions:
        raise ValueError(f"Configuration value '{config_key(set_name, 'extensions')}' must define at least one extension")

    return normalized_extensions


def get_patterns(script_config: dict[str, Any], set_name: str) -> list[re.Pattern[str]]:
    """Compile and validate configured filename timestamp regex patterns."""

    pattern_configs = read_config_list(script_config, set_name, "patterns")

    compiled_patterns: list[re.Pattern] = []
    for index, pattern_config in enumerate(pattern_configs, start=1):
        if not isinstance(pattern_config, dict):
            raise TypeError(f"Pattern entry {index} must be a table with a 'pattern' value")

        pattern_value = pattern_config.get("pattern")
        if not isinstance(pattern_value, str) or not pattern_value:
            raise ValueError(f"Pattern entry {index} must define a non-empty 'pattern' string")

        try:
            compiled_pattern = re.compile(pattern_value, flags=re.IGNORECASE)
        except re.error as error:
            raise ValueError(f"Pattern entry {index} has invalid regex syntax: {error}") from error

        group_names = set(compiled_pattern.groupindex)
        has_year_group = "year" in group_names or "year2" in group_names
        has_month_day_groups = {"month", "day"}.issubset(group_names)

        if not has_year_group or not has_month_day_groups:
            raise ValueError(f"Pattern entry {index} must define named groups year or year2, plus month and day")

        compiled_patterns.append(compiled_pattern)

    return compiled_patterns


def datetime_to_filetime(timestamp: datetime) -> tuple[int, int]:
    """Convert a Python datetime into low/high Windows FILETIME integers."""

    utc_timestamp = timestamp.astimezone(timezone.utc)
    ticks = int((utc_timestamp - WINDOWS_EPOCH).total_seconds() * WINDOWS_TICK)
    return ticks & 0xFFFFFFFF, ticks >> 32


class FileTime(Structure):
    """ctypes representation of the Win32 FILETIME structure."""

    _fields_ = (("dwLowDateTime", c_uint32), ("dwHighDateTime", c_uint32))


def set_file_times(path: Path, timestamp: datetime) -> None:
    """Set creation, access, and modification times for a Windows file."""

    low_date_time, high_date_time = datetime_to_filetime(timestamp)
    filetime = FileTime(low_date_time, high_date_time)
    handle = kernel32.CreateFileW(
        str(path),
        GENERIC_READ_ATTRIBUTES,
        FILE_SHARE_READ_WRITE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )

    if handle == INVALID_HANDLE_VALUE:
        raise OSError(f"Could not open file for timestamp update: {path}")

    try:
        success = kernel32.SetFileTime(handle, byref(filetime), byref(filetime), byref(filetime))
        if not success:
            raise OSError(f"Could not set timestamps: {path}")
    finally:
        kernel32.CloseHandle(handle)


def parse_timestamp(path: Path, patterns: list[re.Pattern[str]], hour_adjustment: int) -> datetime | None:
    """Parse a timestamp from a filename using the first matching pattern."""

    file_name_no_ext = path.stem

    for pattern in patterns:
        match = pattern.match(file_name_no_ext)

        if match is None:
            continue

        values = match.groupdict()
        year = values.get("year")
        year2 = values.get("year2")
        month = values.get("month")
        day = values.get("day")
        hour = values.get("hour") or "00"
        minute = values.get("minute") or "00"
        second = values.get("second") or "00"

        if year is None and year2 is not None:
            year = f"20{year2}"

        if year is None or month is None or day is None:
            raise ValueError(f"Pattern must provide year/year2, month, and day groups: {pattern.pattern}")

        timestamp = datetime.strptime(
            f"{year}{month}{day}{hour}{minute}{second}",
            "%Y%m%d%H%M%S",
        ).astimezone()

        if hour_adjustment:
            timestamp += timedelta(hours=hour_adjustment)

        return timestamp

    return None


def prepare_target_folder(change_files_in_place: bool, target_folder: Path) -> None:
    """Create the target folder when files are copied before modification."""

    if change_files_in_place:
        return

    target_folder.mkdir(parents=True, exist_ok=True)


def collision_safe_path(path: Path) -> Path:
    """Return a non-existing path by appending a numeric suffix when needed."""

    if not path.exists():
        return path

    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def prepare_destination(
    source_file: Path,
    *,
    change_files_in_place: bool,
    target_folder: Path,
    overwrite: bool,
) -> Path | None:
    """Return the file to update, copying it first when not modifying in place."""

    if change_files_in_place:
        return source_file

    destination = target_folder / source_file.name
    if not overwrite:
        destination = collision_safe_path(destination)

    try:
        shutil.copy2(source_file, destination)
    except OSError as error:
        visual.print_warning(f"Skipped {source_file.name}: {error}")
        return None

    return destination


def adjust_file_creation_dates(script_config: dict, set_name: str) -> list[FileAdjustmentResult]:
    """Process all matching files for one adjustment set and collect results."""

    source_folder = source_folder_from_config(script_config)
    change_files_in_place = change_files_in_place_from_config(script_config)
    overwrite = overwrite_from_config(script_config)
    target_folder = target_folder_from_config(script_config, source_folder, change_files_in_place)
    extensions = get_file_extensions(script_config, set_name)
    patterns = get_patterns(script_config, set_name)
    hour_adjustment = hour_adjustment_from_config(script_config)

    results: list[FileAdjustmentResult] = []
    prepare_target_folder(change_files_in_place, target_folder)

    visual.print_info(f"Adjusting file creation dates in {source_folder}", emoji="archive")

    for source_file in source_folder.iterdir():
        if not source_file.is_file() or source_file.suffix.lower() not in extensions:
            continue

        try:
            timestamp = parse_timestamp(source_file, patterns, hour_adjustment)
            if timestamp is None:
                continue

            destination = prepare_destination(
                source_file,
                change_files_in_place=change_files_in_place,
                target_folder=target_folder,
                overwrite=overwrite,
            )
            if destination is None:
                continue

            set_file_times(destination, timestamp)
            results.append(FileAdjustmentResult(source_file, destination, timestamp, changed=True))
            visual.print_success(f"Updated {destination.name} → {timestamp:%Y-%m-%d %H:%M:%S}")
        except Exception as error:
            visual.print_error(f"File creation date adjustment failed: {source_file}: {error}")
            results.append(FileAdjustmentResult(source_file, error=error))

    return results


def summarize_adjustment_results(results: list[FileAdjustmentResult]) -> None:
    """Print a compact summary of successful and failed file adjustments."""

    failed_results = [result for result in results if result.failed]
    changed_count = sum(1 for result in results if result.changed)

    visual.print_info(
        "File creation date adjustment summary: "
        f"{changed_count} updated, {len(failed_results)} failed, {len(results)} matched file(s)",
        emoji="list",
    )

    for result in failed_results:
        visual.print_error(f"Failed timestamp update: {result.source}: {result.error}")


def config_for_adjust_smb(config: dict, script_config: dict, set_name: str) -> dict:
    """Return the scoped config used for optional SMB connection setup."""

    adjust_smb = script_config.get("smb", False)

    if not isinstance(adjust_smb, bool):
        raise TypeError(f"Configuration value '{config_key(set_name, 'smb')}' must be true or false")

    scoped_config = dict(config)

    if adjust_smb:
        return scoped_config

    scoped_config.pop("smb", None)
    return scoped_config


def main() -> None:
    """Run the selected file creation date adjustment set."""

    config = load_config(__file__)
    ensure_section(config)
    set_name = adjustment_set_name(config)
    script_config = adjustment_set_config(config, set_name)

    visual.print_start(f"Starting file creation date adjustment: {set_name}")
    connect_smb.connect_from_config(
        config_for_adjust_smb(config, script_config, set_name),
        on_password_prompted=lambda password: connect_smb.store_prompted_password(config, password),
    )

    results = adjust_file_creation_dates(script_config, set_name)
    summarize_adjustment_results(results)

    if any(result.failed for result in results):
        raise FileCreationDateAdjustmentError(results)

    changed_count = sum(1 for result in results if result.changed)
    visual.print_done(f"File creation date adjustment finished: {set_name}: {changed_count} file(s) updated")


if __name__ == "__main__":
    main()
