import re
import shutil
from ctypes import Structure, WinDLL, byref, c_bool, c_uint32, c_void_p, c_wchar_p
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config_support as config_loader
from winutils_python import connect_smb, visual

DEFAULT_SECTION = r'''adjust_file_creation_date:
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
  hour_adjustment: 0
  patterns:
    - pattern: '^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})(?:[ _-]+(?P<hour>\d{2})_(?P<minute>\d{2})_(?P<second>\d{2}))?'
'''

INVALID_HANDLE_VALUE = c_void_p(-1).value
WINDOWS_TICK = 10_000_000
WINDOWS_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)

kernel32 = WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.argtypes = (c_wchar_p, c_uint32, c_uint32, c_void_p, c_uint32, c_uint32, c_void_p)
kernel32.CreateFileW.restype = c_void_p
kernel32.SetFileTime.argtypes = (c_void_p, c_void_p, c_void_p, c_void_p)
kernel32.SetFileTime.restype = c_bool
kernel32.CloseHandle.argtypes = (c_void_p,)
kernel32.CloseHandle.restype = c_bool


@dataclass(frozen=True)
class FileAdjustmentResult:
    source: Path
    destination: Path | None = None
    timestamp: datetime | None = None
    changed: bool = False
    error: Exception | None = None

    @property
    def failed(self) -> bool:
        return self.error is not None


class FileCreationDateAdjustmentError(RuntimeError):
    def __init__(self, results: list[FileAdjustmentResult]) -> None:
        self.results = results
        failed_results = [result for result in results if result.failed]
        summary = ", ".join(f"{result.source}: {result.error}" for result in failed_results)
        super().__init__(f"{len(failed_results)} file creation date adjustment(s) failed: {summary}")


def store_prompted_smb_password(config: dict, password: str) -> None:
    config_path = config.get("__config_path__")
    if config_path is None:
        raise ValueError("Loaded configuration is missing internal '__config_path__'")

    config_loader.replace_or_add_string_value(config_path, "smb", "encrypted_password", connect_smb.encrypt_password(password))
    config_loader.remove_value(config_path, "smb", "password_file")
    config_loader.remove_value(config_path, "smb", "password")


class FileTime(Structure):
    _fields_ = (
        ("dwLowDateTime", c_uint32),
        ("dwHighDateTime", c_uint32),
    )


def datetime_to_filetime(timestamp: datetime) -> FileTime:
    utc_timestamp = timestamp.astimezone(timezone.utc)
    ticks = int((utc_timestamp - WINDOWS_EPOCH).total_seconds() * WINDOWS_TICK)
    return FileTime(ticks & 0xFFFFFFFF, ticks >> 32)


def set_file_times(path: Path, timestamp: datetime) -> None:
    filetime = datetime_to_filetime(timestamp)
    handle = kernel32.CreateFileW(
        str(path),
        0x0100,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000,
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


def parse_timestamp(path: Path, patterns: list[re.Pattern], hour_adjustment: int) -> datetime | None:
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
    if change_files_in_place:
        return

    target_folder.mkdir(parents=True, exist_ok=True)


def prepare_destination(source_file: Path, *, change_files_in_place: bool, target_folder: Path) -> Path | None:
    if change_files_in_place:
        return source_file

    destination = target_folder / source_file.name

    try:
        shutil.copy2(source_file, destination)
    except OSError as error:
        visual.print_warning(f"Skipped {source_file.name}: {error}")
        return None

    return destination


def get_file_extensions(script_config: dict) -> set[str]:
    extensions = script_config.get("extensions", [])

    if not isinstance(extensions, list):
        raise TypeError("Configuration value 'adjust_file_creation_date.extensions' must be a list")

    normalized_extensions = {str(extension).lower() for extension in extensions if str(extension).strip()}

    if not normalized_extensions:
        raise ValueError("Configuration value 'adjust_file_creation_date.extensions' must define at least one extension")

    return normalized_extensions


def get_patterns(script_config: dict) -> list[re.Pattern]:
    pattern_configs = script_config.get("patterns", [])

    if not isinstance(pattern_configs, list):
        raise TypeError("Configuration value 'adjust_file_creation_date.patterns' must be a list")

    if not pattern_configs:
        raise ValueError("Configuration value 'adjust_file_creation_date.patterns' must define at least one pattern")

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
        if not ({"year", "year2"} & group_names) or not {"month", "day"}.issubset(group_names):
            raise ValueError(
                f"Pattern entry {index} must define named groups year or year2, plus month and day"
            )

        compiled_patterns.append(compiled_pattern)

    return compiled_patterns


def get_target_folder(script_config: dict, source_folder: Path, change_files_in_place: bool) -> Path:
    if change_files_in_place:
        return source_folder

    target_folder = script_config.get("target_folder")

    if target_folder:
        return Path(str(target_folder))

    return source_folder / "changed_date"


def adjust_file_creation_dates(script_config: dict) -> list[FileAdjustmentResult]:
    source_folder = Path(str(script_config["source_folder"]))
    change_files_in_place = bool(script_config.get("change_files_in_place", True))
    target_folder = get_target_folder(script_config, source_folder, change_files_in_place)
    extensions = get_file_extensions(script_config)
    patterns = get_patterns(script_config)
    hour_adjustment = int(script_config.get("hour_adjustment", 0))

    results: list[FileAdjustmentResult] = []
    prepare_target_folder(change_files_in_place, target_folder)

    visual.print_info(f"Adjusting file creation dates in {source_folder}", emoji="archive")

    for source_file in source_folder.iterdir():
        if not source_file.is_file():
            continue

        if source_file.suffix.lower() not in extensions:
            continue

        try:
            timestamp = parse_timestamp(source_file, patterns, hour_adjustment)
            if timestamp is None:
                continue

            destination = prepare_destination(
                source_file,
                change_files_in_place=change_files_in_place,
                target_folder=target_folder,
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
    failed_results = [result for result in results if result.failed]
    changed_count = sum(1 for result in results if result.changed)

    visual.print_info(
        f"File creation date adjustment summary: {changed_count} updated, "
        f"{len(failed_results)} failed, {len(results)} matched file(s)",
        emoji="list",
    )

    for result in failed_results:
        visual.print_error(f"Failed timestamp update: {result.source}: {result.error}")


def ensure_section(config: dict) -> dict:
    if "adjust_file_creation_date" not in config:
        config_loader.append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning("Added default 'adjust_file_creation_date' section to config.yaml. Please configure it before running.")
        raise SystemExit(1)

    return config_loader.get_table(config, "adjust_file_creation_date")


def config_for_adjust_smb(config: dict, script_config: dict) -> dict:
    adjust_smb = script_config.get("smb", False)

    if not isinstance(adjust_smb, bool):
        raise TypeError("Configuration value 'adjust_file_creation_date.smb' must be true or false")

    scoped_config = dict(config)

    if adjust_smb:
        return scoped_config

    scoped_config.pop("smb", None)
    return scoped_config


def main() -> None:
    config = config_loader.load(__file__)
    script_config = ensure_section(config)

    visual.print_start("Starting file creation date adjustment")
    connect_smb.connect_from_config(
        config_for_adjust_smb(config, script_config),
        on_password_prompted=lambda password: store_prompted_smb_password(config, password),
    )

    results = adjust_file_creation_dates(script_config)
    summarize_adjustment_results(results)

    if any(result.failed for result in results):
        raise FileCreationDateAdjustmentError(results)

    changed_count = sum(1 for result in results if result.changed)
    visual.print_done(f"File creation date adjustment finished: {changed_count} file(s) updated")


if __name__ == "__main__":
    main()
