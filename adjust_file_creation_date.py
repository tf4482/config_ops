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


def app_dir(script_file: str | Path) -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(script_file).resolve().parent


def script_dir(script_file: str | Path) -> Path:
    return app_dir(script_file)


def config_path(script_file: str | Path) -> Path:
    return script_dir(script_file) / "config.yaml"


def find_config_path(script_file: str | Path) -> Path:
    path = config_path(script_file)

    if not path.exists():
        path.write_text("", encoding="utf-8")

    return path


def parse_yaml(config_text: str) -> dict[str, Any]:
    return yaml.safe_load(config_text) or {}


def dump_yaml(config: dict[str, Any]) -> str:
    clean = {key: value for key, value in config.items() if not key.startswith("__")}
    return yaml.safe_dump(clean, sort_keys=False, allow_unicode=True)


def load_config(script_file: str | Path) -> dict[str, Any]:
    path = find_config_path(script_file)
    loaded_config = parse_yaml(path.read_text(encoding="utf-8"))
    loaded_config["__config_path__"] = path
    return loaded_config


def append_section_yaml(config: dict[str, Any], section_yaml: str) -> None:
    path = config.get("__config_path__")

    if not isinstance(path, Path):
        return

    existing = path.read_text(encoding="utf-8").rstrip()
    separator = "\n\n" if existing else ""
    path.write_text(existing + separator + section_yaml.strip() + "\n", encoding="utf-8")


def replace_or_add_string_value(path: Path, table: str, key: str, value: str) -> None:
    loaded_config = parse_yaml(path.read_text(encoding="utf-8"))
    table_config = loaded_config.setdefault(table, {})

    if not isinstance(table_config, dict):
        raise TypeError(f"Configuration value '{table}' must be a table")

    table_config[key] = value
    path.write_text(dump_yaml(loaded_config), encoding="utf-8")


def remove_value(path: Path, table: str, key: str) -> None:
    loaded_config = parse_yaml(path.read_text(encoding="utf-8"))
    table_config = loaded_config.get(table, {})

    if isinstance(table_config, dict) and key in table_config:
        del table_config[key]
        path.write_text(dump_yaml(loaded_config), encoding="utf-8")


def get_table(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name, {})

    if not isinstance(value, dict):
        raise TypeError(f"Configuration value '{name}' must be a table")

    return value


class config_loader:
    load = staticmethod(load_config)
    append_section_yaml = staticmethod(append_section_yaml)
    get_table = staticmethod(get_table)


def ensure_section(config: dict[str, Any]) -> None:
    if "adjust_file_creation_date" not in config:
        append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning("Added default 'adjust_file_creation_date' section to config.yaml. Please configure it before running.")
        raise SystemExit(1)

    get_table(config, "adjust_file_creation_date")


def validate_adjustment_set_name(config: dict[str, Any], set_name: str) -> str:
    adjustment_sets = get_table(config, "adjust_file_creation_date")

    if set_name not in adjustment_sets:
        available_sets = ", ".join(adjustment_sets) or "none"
        raise SystemExit(f"Unknown file creation date adjustment set '{set_name}'. Available sets: {available_sets}")

    return set_name


def adjustment_set_config(config: dict[str, Any], set_name: str) -> dict[str, Any]:
    adjustment_sets = get_table(config, "adjust_file_creation_date")
    adjustment_set = adjustment_sets.get(set_name)

    if not isinstance(adjustment_set, dict):
        raise TypeError(f"File creation date adjustment set '{set_name}' must be a table")

    return adjustment_set


def choose_adjustment_set_terminal(config: dict[str, Any]) -> str:
    adjustment_sets = get_table(config, "adjust_file_creation_date")
    return menu.choose_mapping_key_terminal(
        adjustment_sets,
        header="Available file creation date adjustment sets:",
        empty_message="No file creation date adjustment sets configured in config.yaml. Please add an 'adjust_file_creation_date' section.",
    )


def adjustment_set_name(config: dict[str, Any]) -> str:
    if len(sys.argv) > 1:
        return validate_adjustment_set_name(config, menu.normalize_selection_name(sys.argv[1]))

    return choose_adjustment_set_terminal(config)


def registry_path_from_config(script_config: dict[str, Any], set_name: str) -> str:
    registry_path = str(script_config.get("smb_registry_path", "Software\\peripherals")).strip()

    if not registry_path:
        raise ValueError(f"Configuration value 'adjust_file_creation_date.{set_name}.smb_registry_path' must be set")

    return registry_path


def source_folder_from_config(script_config: dict[str, Any]) -> Path:
    source_folder = Path(str(script_config["source_folder"]))
    return source_folder


def change_files_in_place_from_config(script_config: dict[str, Any]) -> bool:
    return bool(script_config.get("change_files_in_place", True))


def overwrite_from_config(script_config: dict[str, Any]) -> bool:
    return bool(script_config.get("overwrite", False))


def target_folder_from_config(script_config: dict[str, Any], source_folder: Path, change_files_in_place: bool) -> Path:
    if change_files_in_place:
        return source_folder

    target_folder = script_config.get("target_folder")

    if target_folder:
        return Path(str(target_folder))

    return source_folder / "changed_date"


def hour_adjustment_from_config(script_config: dict[str, Any]) -> int:
    return int(script_config.get("hour_adjustment", 0))


def read_config_list(script_config: dict[str, Any], set_name: str, name: str) -> list[Any]:
    value = script_config.get(name, [])

    if not isinstance(value, list):
        raise TypeError(f"Configuration value 'adjust_file_creation_date.{set_name}.{name}' must be a list")

    if not value:
        raise ValueError(f"Configuration value 'adjust_file_creation_date.{set_name}.{name}' must define at least one entry")

    return value


def get_file_extensions(script_config: dict, set_name: str) -> set[str]:
    extensions = read_config_list(script_config, set_name, "extensions")
    normalized_extensions = {str(extension).lower() for extension in extensions if str(extension).strip()}

    if not normalized_extensions:
        raise ValueError(f"Configuration value 'adjust_file_creation_date.{set_name}.extensions' must define at least one extension")

    return normalized_extensions


def get_patterns(script_config: dict, set_name: str) -> list[re.Pattern]:
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
        if not ("year" in group_names or "year2" in group_names) or not {"month", "day"}.issubset(group_names):
            raise ValueError(f"Pattern entry {index} must define named groups year or year2, plus month and day")

        compiled_patterns.append(compiled_pattern)

    return compiled_patterns


def device_state_key(path: Path) -> str:
    return str(path)


def cleanup_destination(path: Path) -> None:
    if path.exists() and path.is_dir():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def datetime_to_filetime(timestamp: datetime) -> tuple[int, int]:
    utc_timestamp = timestamp.astimezone(timezone.utc)
    ticks = int((utc_timestamp - WINDOWS_EPOCH).total_seconds() * WINDOWS_TICK)
    return ticks & 0xFFFFFFFF, ticks >> 32


class FileTime(Structure):
    _fields_ = (("dwLowDateTime", c_uint32), ("dwHighDateTime", c_uint32))


def set_file_times(path: Path, timestamp: datetime) -> None:
    low_date_time, high_date_time = datetime_to_filetime(timestamp)
    filetime = FileTime(low_date_time, high_date_time)
    handle = kernel32.CreateFileW(str(path), 0x0100, 0x00000001 | 0x00000002 | 0x00000004, None, 3, 0x02000000, None)

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

        timestamp = datetime.strptime(f"{year}{month}{day}{hour}{minute}{second}", "%Y%m%d%H%M%S").astimezone()

        if hour_adjustment:
            timestamp += timedelta(hours=hour_adjustment)

        return timestamp

    return None


def prepare_target_folder(change_files_in_place: bool, target_folder: Path) -> None:
    if change_files_in_place:
        return

    target_folder.mkdir(parents=True, exist_ok=True)


def collision_safe_path(path: Path) -> Path:
    if not path.exists():
        return path

    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def prepare_destination(source_file: Path, *, change_files_in_place: bool, target_folder: Path, overwrite: bool) -> Path | None:
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
    failed_results = [result for result in results if result.failed]
    changed_count = sum(1 for result in results if result.changed)

    visual.print_info(
        f"File creation date adjustment summary: {changed_count} updated, {len(failed_results)} failed, {len(results)} matched file(s)",
        emoji="list",
    )

    for result in failed_results:
        visual.print_error(f"Failed timestamp update: {result.source}: {result.error}")


def store_prompted_smb_password(config: dict, password: str) -> None:
    config_path = config.get("__config_path__")

    if config_path is None:
        raise ValueError("Loaded configuration is missing internal '__config_path__'")

    replace_or_add_string_value(config_path, "smb", "encrypted_password", connect_smb.encrypt_password(password))
    remove_value(config_path, "smb", "password_file")
    remove_value(config_path, "smb", "password")


def config_for_adjust_smb(config: dict, script_config: dict, set_name: str) -> dict:
    adjust_smb = script_config.get("smb", False)

    if not isinstance(adjust_smb, bool):
        raise TypeError(f"Configuration value 'adjust_file_creation_date.{set_name}.smb' must be true or false")

    scoped_config = dict(config)

    if adjust_smb:
        return scoped_config

    scoped_config.pop("smb", None)
    return scoped_config


def main() -> None:
    config = load_config(__file__)
    ensure_section(config)
    set_name = adjustment_set_name(config)
    script_config = adjustment_set_config(config, set_name)

    visual.print_start(f"Starting file creation date adjustment: {set_name}")
    connect_smb.connect_from_config(
        config_for_adjust_smb(config, script_config, set_name),
        on_password_prompted=lambda password: store_prompted_smb_password(config, password),
    )

    results = adjust_file_creation_dates(script_config, set_name)
    summarize_adjustment_results(results)

    if any(result.failed for result in results):
        raise FileCreationDateAdjustmentError(results)

    changed_count = sum(1 for result in results if result.changed)
    visual.print_done(f"File creation date adjustment finished: {set_name}: {changed_count} file(s) updated")


if __name__ == "__main__":
    main()
