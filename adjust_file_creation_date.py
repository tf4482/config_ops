"""Adjust Windows file timestamps from date/time values parsed from filenames.

The script reads named configuration sets from ``config.yaml``, optionally connects
SMB mappings, copies files when requested, and applies parsed timestamps as
creation, access, and modification times through the Win32 ``SetFileTime`` API.
"""

import re
import shutil
import struct
from ctypes import Structure, WinDLL, byref, c_bool, c_uint32, c_void_p, c_wchar_p
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from winutils_python import config as config_utils
from winutils_python import config_sets, connect_smb, visual


DEFAULT_SECTION = r'''adjust_file_creation_date:
  example_set:
    smb: false
    mode: file
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
      - .mp4
      - .mov
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
MODE_FILE = "file"
MODE_FOLDER = "folder"
MODE_METADATA = "metadata"
VALID_MODES = {MODE_FILE, MODE_FOLDER, MODE_METADATA}
DEFAULT_SMB_REGISTRY_PATH = "Software\\peripherals"
CANCEL_CHOICES = {"exit", "quit", "cancel"}
GENERIC_READ_ATTRIBUTES = 0x0100
FILE_SHARE_READ_WRITE_DELETE = 0x00000001 | 0x00000002 | 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
EXIF_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff"}
QUICKTIME_VIDEO_EXTENSIONS = {".3g2", ".3gp", ".m4v", ".mov", ".mp4"}
EXIF_DATETIME_TAGS = (0x9003, 0x9004, 0x0132)
METADATA_DATETIME_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
QUICKTIME_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)

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

def ensure_section(config: dict[str, Any]) -> None:
    """Ensure the adjustment section exists and has table shape."""

    if CONFIG_SECTION not in config:
        config_utils.append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning(
            f"Added default '{CONFIG_SECTION}' section to config.yaml. "
            "Please configure it before running."
        )
        raise SystemExit(1)

    config_sets.section_sets(config, CONFIG_SECTION)


def config_key(set_name: str, name: str) -> str:
    """Return a human-readable dotted config key for error messages."""

    return config_sets.config_key(CONFIG_SECTION, set_name, name)


def registry_path_from_config(script_config: dict[str, Any], set_name: str) -> str:
    """Return the registry path used by related SMB/peripheral integrations."""

    registry_path = str(script_config.get("smb_registry_path", DEFAULT_SMB_REGISTRY_PATH)).strip()

    if not registry_path:
        raise ValueError(f"Configuration value '{config_key(set_name, 'smb_registry_path')}' must be set")

    return registry_path


def source_folder_from_config(script_config: dict[str, Any]) -> Path:
    """Return the source folder configured for the adjustment set."""

    return Path(str(script_config["source_folder"]))


def mode_from_config(script_config: dict[str, Any], set_name: str) -> str:
    """Return the configured timestamp source mode."""

    mode = str(script_config.get("mode", MODE_FILE)).strip().lower()

    if mode not in VALID_MODES:
        valid_modes = ", ".join(sorted(VALID_MODES))
        raise ValueError(f"Configuration value '{config_key(set_name, 'mode')}' must be one of: {valid_modes}")

    return mode


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


def get_file_extensions(script_config: dict[str, Any], set_name: str) -> set[str]:
    """Return normalized lowercase file extensions to process."""

    return config_utils.normalized_extension_set(
        script_config,
        "extensions",
        label=config_key(set_name, "extensions"),
        non_empty=True,
    )


def get_patterns(script_config: dict[str, Any], set_name: str) -> list[re.Pattern[str]]:
    """Compile and validate configured filename timestamp regex patterns."""

    pattern_configs = config_utils.required_list(
        script_config,
        "patterns",
        label=config_key(set_name, "patterns"),
        non_empty=True,
    )

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


def get_patterns_for_mode(script_config: dict[str, Any], set_name: str, mode: str) -> list[re.Pattern[str]]:
    """Return regex patterns unless the selected mode reads embedded metadata."""

    if mode == MODE_METADATA:
        return []

    return get_patterns(script_config, set_name)


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


def parse_timestamp_text(value: str, patterns: list[re.Pattern[str]], hour_adjustment: int) -> datetime | None:
    """Parse a timestamp from text using the first matching pattern."""

    for pattern in patterns:
        match = pattern.match(value)

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


def parse_timestamp(path: Path, patterns: list[re.Pattern[str]], hour_adjustment: int) -> datetime | None:
    """Parse a timestamp from a filename using the first matching pattern."""

    return parse_timestamp_text(path.stem, patterns, hour_adjustment)


def parse_folder_timestamp(folder: Path, patterns: list[re.Pattern[str]], hour_adjustment: int) -> datetime | None:
    """Parse a timestamp from a folder name using the first matching pattern."""

    return parse_timestamp_text(folder.name, patterns, hour_adjustment)


def metadata_timestamp(path: Path, hour_adjustment: int) -> datetime | None:
    """Return the first supported embedded image or video timestamp."""

    suffix = path.suffix.lower()

    if suffix in EXIF_IMAGE_EXTENSIONS:
        timestamp = exif_timestamp(path)
    elif suffix in QUICKTIME_VIDEO_EXTENSIONS:
        timestamp = quicktime_timestamp(path)
    else:
        timestamp = None

    if timestamp is not None and hour_adjustment:
        timestamp += timedelta(hours=hour_adjustment)

    return timestamp


def exif_timestamp(path: Path) -> datetime | None:
    """Read common EXIF date/time tags from JPEG or TIFF metadata."""

    try:
        data = path.read_bytes()
    except OSError:
        return None

    tiff_data = tiff_bytes_from_image(data)
    if tiff_data is None:
        return None

    return timestamp_from_tiff(tiff_data)


def tiff_bytes_from_image(data: bytes) -> bytes | None:
    """Return TIFF/EXIF bytes from JPEG APP1 data or a TIFF image."""

    if data.startswith((b"II*\x00", b"MM\x00*")):
        return data

    if not data.startswith(b"\xff\xd8"):
        return None

    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            break

        marker = data[offset + 1]
        offset += 2

        if marker in {0xD8, 0xD9}:
            continue

        if offset + 2 > len(data):
            break

        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        segment_start = offset + 2
        segment_end = offset + segment_length

        if segment_length < 2 or segment_end > len(data):
            break

        segment = data[segment_start:segment_end]
        if marker == 0xE1 and segment.startswith(b"Exif\x00\x00"):
            return segment[6:]

        offset = segment_end

    return None


def timestamp_from_tiff(data: bytes) -> datetime | None:
    """Read a timestamp from TIFF IFD metadata."""

    endian = tiff_endian(data)
    if endian is None or len(data) < 8:
        return None

    first_ifd_offset = int.from_bytes(data[4:8], endian)
    return timestamp_from_ifd_chain(data, endian, first_ifd_offset, visited=set())


def tiff_endian(data: bytes) -> str | None:
    """Return the byte order for TIFF metadata."""

    if data.startswith(b"II*\x00"):
        return "little"

    if data.startswith(b"MM\x00*"):
        return "big"

    return None


def timestamp_from_ifd_chain(data: bytes, endian: str, offset: int, *, visited: set[int]) -> datetime | None:
    """Search an IFD and linked/sub IFDs for EXIF date/time tags."""

    if offset in visited or offset + 2 > len(data):
        return None

    visited.add(offset)

    entry_count = int.from_bytes(data[offset : offset + 2], endian)
    entries_start = offset + 2
    entries_end = entries_start + entry_count * 12

    if entries_end > len(data):
        return None

    linked_ifd_offsets: list[int] = []

    for entry_offset in range(entries_start, entries_end, 12):
        tag, field_type, count, value_offset = tiff_ifd_entry(data, endian, entry_offset)

        if tag in EXIF_DATETIME_TAGS:
            timestamp = parse_metadata_datetime(tiff_ascii_value(data, endian, field_type, count, value_offset))
            if timestamp is not None:
                return timestamp

        if tag in {0x8769, 0x8825} and value_offset:
            linked_ifd_offsets.append(value_offset)

    next_ifd_pointer = entries_end
    if next_ifd_pointer + 4 <= len(data):
        next_ifd_offset = int.from_bytes(data[next_ifd_pointer : next_ifd_pointer + 4], endian)
        if next_ifd_offset:
            linked_ifd_offsets.append(next_ifd_offset)

    for linked_offset in linked_ifd_offsets:
        timestamp = timestamp_from_ifd_chain(data, endian, linked_offset, visited=visited)
        if timestamp is not None:
            return timestamp

    return None


def tiff_ifd_entry(data: bytes, endian: str, offset: int) -> tuple[int, int, int, int]:
    """Return tag, type, count, and integer value/offset from one TIFF IFD entry."""

    tag = int.from_bytes(data[offset : offset + 2], endian)
    field_type = int.from_bytes(data[offset + 2 : offset + 4], endian)
    count = int.from_bytes(data[offset + 4 : offset + 8], endian)
    value_offset = int.from_bytes(data[offset + 8 : offset + 12], endian)
    return tag, field_type, count, value_offset


def tiff_ascii_value(data: bytes, endian: str, field_type: int, count: int, value_offset: int) -> str | None:
    """Return an ASCII string from a TIFF IFD entry value."""

    if field_type != 2 or count <= 0:
        return None

    if count <= 4:
        raw_value = value_offset.to_bytes(4, endian)[:count]
    elif value_offset + count <= len(data):
        raw_value = data[value_offset : value_offset + count]
    else:
        return None

    return raw_value.rstrip(b"\x00").decode("ascii", errors="ignore").strip()


def parse_metadata_datetime(value: str | None) -> datetime | None:
    """Parse common image/video metadata date-time text as local time."""

    if not value:
        return None

    normalized = value.strip().removesuffix("Z").strip()

    for date_format in METADATA_DATETIME_FORMATS:
        try:
            return datetime.strptime(normalized[:19], date_format).astimezone()
        except ValueError:
            continue

    return None


def quicktime_timestamp(path: Path) -> datetime | None:
    """Read a QuickTime/MP4 creation timestamp from movie header metadata."""

    try:
        with path.open("rb") as file:
            return quicktime_timestamp_from_range(file, 0, path.stat().st_size)
    except OSError:
        return None


def quicktime_timestamp_from_range(file: Any, start: int, end: int) -> datetime | None:
    """Search a byte range of QuickTime atoms for a movie-header creation time."""

    offset = start
    while offset + 8 <= end:
        atom_size, atom_type, header_size = read_quicktime_atom_header(file, offset, end)
        if atom_size is None or atom_type is None or header_size is None:
            break

        atom_end = offset + atom_size
        payload_start = offset + header_size

        if atom_end > end or atom_size < header_size:
            break

        if atom_type == b"mvhd":
            timestamp = quicktime_mvhd_timestamp(file, payload_start, atom_end)
            if timestamp is not None:
                return timestamp
        elif atom_type in {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"meta", b"ilst"}:
            child_start = payload_start + (4 if atom_type == b"meta" else 0)
            timestamp = quicktime_timestamp_from_range(file, child_start, atom_end)
            if timestamp is not None:
                return timestamp

        offset = atom_end

    return None


def read_quicktime_atom_header(file: Any, offset: int, end: int) -> tuple[int | None, bytes | None, int | None]:
    """Read a QuickTime atom header at an offset."""

    file.seek(offset)
    header = file.read(8)
    if len(header) != 8:
        return None, None, None

    atom_size, atom_type = struct.unpack(">I4s", header)

    if atom_size == 1:
        extended_size_data = file.read(8)
        if len(extended_size_data) != 8:
            return None, None, None
        atom_size = struct.unpack(">Q", extended_size_data)[0]
        header_size = 16
    elif atom_size == 0:
        atom_size = end - offset
        header_size = 8
    else:
        header_size = 8

    return atom_size, atom_type, header_size


def quicktime_mvhd_timestamp(file: Any, start: int, end: int) -> datetime | None:
    """Read the creation time from a QuickTime mvhd atom payload."""

    file.seek(start)
    version_data = file.read(1)
    if len(version_data) != 1:
        return None

    version = version_data[0]
    file.seek(start + 4)

    if version == 1:
        if start + 12 > end:
            return None
        raw_timestamp = file.read(8)
        if len(raw_timestamp) != 8:
            return None
        seconds = struct.unpack(">Q", raw_timestamp)[0]
    else:
        if start + 8 > end:
            return None
        raw_timestamp = file.read(4)
        if len(raw_timestamp) != 4:
            return None
        seconds = struct.unpack(">I", raw_timestamp)[0]

    if seconds == 0:
        return None

    return (QUICKTIME_EPOCH + timedelta(seconds=seconds)).astimezone()


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
    relative_root: Path | None = None,
) -> Path | None:
    """Return the file to update, copying it first when not modifying in place."""

    if change_files_in_place:
        return source_file

    destination = target_folder / source_file.name
    if relative_root is not None:
        destination = target_folder / source_file.relative_to(relative_root)

    if not overwrite:
        destination = collision_safe_path(destination)

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, destination)
    except OSError as error:
        visual.print_warning(f"Skipped {source_file.name}: {error}")
        return None

    return destination


def is_relative_to(path: Path, root: Path) -> bool:
    """Return whether path is located below root."""

    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False

    return True


def matching_files(source_folder: Path, extensions: set[str], *, recursive: bool) -> tuple[Path, ...]:
    """Return matching source files, optionally including all subfolders."""

    paths = source_folder.rglob("*") if recursive else source_folder.iterdir()
    return tuple(path for path in paths if path.is_file() and path.suffix.lower() in extensions)


def adjust_one_file(
    source_file: Path,
    timestamp: datetime,
    *,
    change_files_in_place: bool,
    target_folder: Path,
    overwrite: bool,
    relative_root: Path | None = None,
) -> FileAdjustmentResult | None:
    """Copy when needed, update one file timestamp, and return its result."""

    destination = prepare_destination(
        source_file,
        change_files_in_place=change_files_in_place,
        target_folder=target_folder,
        overwrite=overwrite,
        relative_root=relative_root,
    )
    if destination is None:
        return None

    set_file_times(destination, timestamp)
    visual.print_success(f"Updated {destination.name} → {timestamp:%Y-%m-%d %H:%M:%S}")
    return FileAdjustmentResult(source_file, destination, timestamp, changed=True)


def adjust_files_from_filenames(
    source_folder: Path,
    *,
    change_files_in_place: bool,
    target_folder: Path,
    overwrite: bool,
    extensions: set[str],
    patterns: list[re.Pattern[str]],
    hour_adjustment: int,
) -> list[FileAdjustmentResult]:
    """Adjust matching files using timestamps parsed from each filename."""

    results: list[FileAdjustmentResult] = []

    for source_file in matching_files(source_folder, extensions, recursive=False):
        try:
            timestamp = parse_timestamp(source_file, patterns, hour_adjustment)
            if timestamp is None:
                continue

            result = adjust_one_file(
                source_file,
                timestamp,
                change_files_in_place=change_files_in_place,
                target_folder=target_folder,
                overwrite=overwrite,
            )
            if result is not None:
                results.append(result)
        except Exception as error:
            visual.print_error(f"File creation date adjustment failed: {source_file}: {error}")
            results.append(FileAdjustmentResult(source_file, error=error))

    return results


def adjust_files_from_folder_names(
    source_folder: Path,
    *,
    change_files_in_place: bool,
    target_folder: Path,
    overwrite: bool,
    extensions: set[str],
    patterns: list[re.Pattern[str]],
    hour_adjustment: int,
) -> list[FileAdjustmentResult]:
    """Adjust matching files recursively using timestamps parsed from containing folder names."""

    results: list[FileAdjustmentResult] = []
    source_files = matching_files(source_folder, extensions, recursive=True)

    for source_file in source_files:
        if not change_files_in_place and is_relative_to(source_file, target_folder):
            continue

        try:
            timestamp = parse_folder_timestamp(source_file.parent, patterns, hour_adjustment)
            if timestamp is None:
                continue

            result = adjust_one_file(
                source_file,
                timestamp,
                change_files_in_place=change_files_in_place,
                target_folder=target_folder,
                overwrite=overwrite,
                relative_root=source_folder,
            )
            if result is not None:
                results.append(result)
        except Exception as error:
            visual.print_error(f"Folder-based file creation date adjustment failed: {source_file}: {error}")
            results.append(FileAdjustmentResult(source_file, error=error))

    return results


def adjust_files_from_metadata(
    source_folder: Path,
    *,
    change_files_in_place: bool,
    target_folder: Path,
    overwrite: bool,
    extensions: set[str],
    hour_adjustment: int,
) -> list[FileAdjustmentResult]:
    """Adjust matching files recursively using embedded image or video metadata timestamps."""

    results: list[FileAdjustmentResult] = []
    source_files = matching_files(source_folder, extensions, recursive=True)

    for source_file in source_files:
        if not change_files_in_place and is_relative_to(source_file, target_folder):
            continue

        try:
            timestamp = metadata_timestamp(source_file, hour_adjustment)
            if timestamp is None:
                continue

            result = adjust_one_file(
                source_file,
                timestamp,
                change_files_in_place=change_files_in_place,
                target_folder=target_folder,
                overwrite=overwrite,
                relative_root=source_folder,
            )
            if result is not None:
                results.append(result)
        except Exception as error:
            visual.print_error(f"Metadata-based file creation date adjustment failed: {source_file}: {error}")
            results.append(FileAdjustmentResult(source_file, error=error))

    return results


def adjust_file_creation_dates(script_config: dict, set_name: str) -> list[FileAdjustmentResult]:
    """Process all matching files for one adjustment set and collect results."""

    source_folder = source_folder_from_config(script_config)
    mode = mode_from_config(script_config, set_name)
    change_files_in_place = change_files_in_place_from_config(script_config)
    overwrite = overwrite_from_config(script_config)
    target_folder = target_folder_from_config(script_config, source_folder, change_files_in_place)
    extensions = get_file_extensions(script_config, set_name)
    patterns = get_patterns_for_mode(script_config, set_name, mode)
    hour_adjustment = hour_adjustment_from_config(script_config)

    prepare_target_folder(change_files_in_place, target_folder)

    visual.print_info(f"Adjusting file creation dates in {source_folder} using {mode} mode", emoji="archive")

    if mode == MODE_FOLDER:
        return adjust_files_from_folder_names(
            source_folder,
            change_files_in_place=change_files_in_place,
            target_folder=target_folder,
            overwrite=overwrite,
            extensions=extensions,
            patterns=patterns,
            hour_adjustment=hour_adjustment,
        )

    if mode == MODE_METADATA:
        return adjust_files_from_metadata(
            source_folder,
            change_files_in_place=change_files_in_place,
            target_folder=target_folder,
            overwrite=overwrite,
            extensions=extensions,
            hour_adjustment=hour_adjustment,
        )

    return adjust_files_from_filenames(
        source_folder,
        change_files_in_place=change_files_in_place,
        target_folder=target_folder,
        overwrite=overwrite,
        extensions=extensions,
        patterns=patterns,
        hour_adjustment=hour_adjustment,
    )


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


def main() -> None:
    """Run the selected file creation date adjustment set."""

    config = config_utils.load(__file__)
    ensure_section(config)
    set_name = config_sets.selected_set_name(
        config,
        CONFIG_SECTION,
        label="file creation date adjustment set",
        header="Available file creation date adjustment sets:",
        empty_message=(
            "No file creation date adjustment sets configured in config.yaml. "
            f"Please add an '{CONFIG_SECTION}' section."
        ),
    )
    script_config = config_sets.get_set_config(
        config,
        CONFIG_SECTION,
        set_name,
        label="File creation date adjustment set",
    )

    visual.print_start(f"Starting file creation date adjustment: {set_name}")
    connect_smb.connect_from_config(
        connect_smb.scoped_config_for_optional_smb(
            config,
            script_config,
            error_label=f"Configuration value '{config_key(set_name, 'smb')}'",
        ),
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
