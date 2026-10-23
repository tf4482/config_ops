from __future__ import annotations

import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from exif import Image

# Extensions with readable or writable embedded timestamps
EXIF_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff"}
QUICKTIME_VIDEO_EXTENSIONS = {".3g2", ".3gp", ".m4v", ".mov", ".mp4"}
EXIF_DATETIME_TAGS = (0x9003, 0x9004, 0x0132)
METADATA_DATETIME_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
QUICKTIME_EPOCH = datetime(1904, 1, 1, tzinfo=UTC)
QUICKTIME_CONTAINER_ATOMS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"meta", b"ilst"}
QUICKTIME_CREATION_TIME_ATOMS = {b"mvhd", b"tkhd", b"mdhd"}


# Return the first supported embedded image or video timestamp
def read_timestamp(path: Path) -> datetime | None:
    suffix = path.suffix.lower()

    if suffix in EXIF_IMAGE_EXTENSIONS:
        timestamp = _exif_timestamp(path)
    elif suffix in QUICKTIME_VIDEO_EXTENSIONS:
        timestamp = _quicktime_timestamp(path)
    else:
        timestamp = None

    return timestamp


# Write supported image/video metadata timestamp fields
def write_timestamp(path: Path, timestamp: datetime) -> bool:
    suffix = path.suffix.lower()

    if suffix in EXIF_IMAGE_EXTENSIONS:
        _write_image_metadata_timestamp(path, timestamp)
        return True

    if suffix in QUICKTIME_VIDEO_EXTENSIONS:
        _write_quicktime_metadata_timestamp(path, timestamp)
        return True

    return False


# Read common EXIF date/time tags from JPEG or TIFF metadata
def _exif_timestamp(path: Path) -> datetime | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None

    tiff_data = _tiff_bytes_from_image(data)
    if tiff_data is None:
        return None

    return _timestamp_from_tiff(tiff_data)


# Return TIFF/EXIF bytes from JPEG APP1 data or a TIFF image
def _tiff_bytes_from_image(data: bytes) -> bytes | None:
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


# Read a timestamp from TIFF IFD metadata
def _timestamp_from_tiff(data: bytes) -> datetime | None:
    endian = _tiff_endian(data)
    if endian is None or len(data) < 8:
        return None

    first_ifd_offset = int.from_bytes(data[4:8], endian)
    return _timestamp_from_ifd_chain(data, endian, first_ifd_offset, visited=set())


# Return the byte order for TIFF metadata
def _tiff_endian(data: bytes) -> Literal["little", "big"] | None:
    if data.startswith(b"II*\x00"):
        return "little"

    if data.startswith(b"MM\x00*"):
        return "big"

    return None


# Search an IFD and linked/sub IFDs for EXIF date/time tags
def _timestamp_from_ifd_chain(
    data: bytes,
    endian: Literal["little", "big"],
    offset: int,
    *,
    visited: set[int],
) -> datetime | None:
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
        tag, field_type, count, value_offset = _tiff_ifd_entry(data, endian, entry_offset)

        if tag in EXIF_DATETIME_TAGS:
            timestamp = _parse_metadata_datetime(
                _tiff_ascii_value(data, endian, field_type, count, value_offset)
            )
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
        timestamp = _timestamp_from_ifd_chain(data, endian, linked_offset, visited=visited)
        if timestamp is not None:
            return timestamp

    return None


# Return tag, type, count, and integer value/offset from one TIFF IFD entry
def _tiff_ifd_entry(
    data: bytes,
    endian: Literal["little", "big"],
    offset: int,
) -> tuple[int, int, int, int]:
    tag = int.from_bytes(data[offset : offset + 2], endian)
    field_type = int.from_bytes(data[offset + 2 : offset + 4], endian)
    count = int.from_bytes(data[offset + 4 : offset + 8], endian)
    value_offset = int.from_bytes(data[offset + 8 : offset + 12], endian)
    return tag, field_type, count, value_offset


# Return an ASCII string from a TIFF IFD entry value
def _tiff_ascii_value(
    data: bytes,
    endian: Literal["little", "big"],
    field_type: int,
    count: int,
    value_offset: int,
) -> str | None:
    if field_type != 2 or count <= 0:
        return None

    if count <= 4:
        raw_value = value_offset.to_bytes(4, endian)[:count]
    elif value_offset + count <= len(data):
        raw_value = data[value_offset : value_offset + count]
    else:
        return None

    return raw_value.rstrip(b"\x00").decode("ascii", errors="ignore").strip()


# Parse common image/video metadata date-time text as local time
def _parse_metadata_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    normalized = value.strip().removesuffix("Z").strip()

    for date_format in METADATA_DATETIME_FORMATS:
        try:
            return datetime.strptime(normalized[:19], date_format).astimezone()
        except ValueError:
            continue

    return None


# Return EXIF-compatible local date/time text
def _metadata_datetime_text(timestamp: datetime) -> str:
    return timestamp.astimezone().strftime("%Y:%m:%d %H:%M:%S")


# Write common EXIF date-taken tags using the Python exif package
def _write_image_metadata_timestamp(path: Path, timestamp: datetime) -> None:
    timestamp_text = _metadata_datetime_text(timestamp)

    with path.open("rb") as file:
        image = Image(file)

    image.datetime = timestamp_text
    image.datetime_original = timestamp_text
    image.datetime_digitized = timestamp_text

    path.write_bytes(image.get_file())


# Return QuickTime epoch seconds for a local or aware datetime
def _quicktime_seconds(timestamp: datetime) -> int:
    seconds = int((timestamp.astimezone(UTC) - QUICKTIME_EPOCH).total_seconds())
    if seconds < 0:
        raise ValueError(f"Timestamp predates the QuickTime epoch: {timestamp:%Y-%m-%d %H:%M:%S}")

    return seconds


# Read a QuickTime/MP4 creation timestamp from movie header metadata
def _quicktime_timestamp(path: Path) -> datetime | None:
    try:
        with path.open("rb") as file:
            return _quicktime_timestamp_from_range(file, 0, path.stat().st_size)
    except OSError:
        return None


# Search a byte range of QuickTime atoms for a movie-header creation time
def _quicktime_timestamp_from_range(file: Any, start: int, end: int) -> datetime | None:
    offset = start
    while offset + 8 <= end:
        atom_size, atom_type, header_size = _read_quicktime_atom_header(file, offset, end)
        if atom_size is None or atom_type is None or header_size is None:
            break

        atom_end = offset + atom_size
        payload_start = offset + header_size

        if atom_end > end or atom_size < header_size:
            break

        if atom_type == b"mvhd":
            timestamp = _quicktime_mvhd_timestamp(file, payload_start, atom_end)
            if timestamp is not None:
                return timestamp
        elif atom_type in QUICKTIME_CONTAINER_ATOMS:
            child_start = payload_start + (4 if atom_type == b"meta" else 0)
            timestamp = _quicktime_timestamp_from_range(file, child_start, atom_end)
            if timestamp is not None:
                return timestamp

        offset = atom_end

    return None


# Read a QuickTime atom header at an offset
def _read_quicktime_atom_header(
    file: Any, offset: int, end: int
) -> tuple[int | None, bytes | None, int | None]:
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


# Read the creation time from a QuickTime mvhd atom payload
def _quicktime_mvhd_timestamp(file: Any, start: int, end: int) -> datetime | None:
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


# Write QuickTime-family media creation timestamps in place
def _write_quicktime_metadata_timestamp(path: Path, timestamp: datetime) -> None:
    seconds = _quicktime_seconds(timestamp)
    with path.open("r+b") as file:
        changed = _write_quicktime_timestamp_in_range(file, 0, path.stat().st_size, seconds)

    if not changed:
        raise ValueError(f"No writable QuickTime creation metadata found: {path}")


# Write creation timestamps in supported QuickTime atoms inside a byte range
def _write_quicktime_timestamp_in_range(file: Any, start: int, end: int, seconds: int) -> bool:
    changed = False
    offset = start

    while offset + 8 <= end:
        atom_size, atom_type, header_size = _read_quicktime_atom_header(file, offset, end)
        if atom_size is None or atom_type is None or header_size is None:
            break

        atom_end = offset + atom_size
        payload_start = offset + header_size

        if atom_end > end or atom_size < header_size:
            break

        if atom_type in QUICKTIME_CREATION_TIME_ATOMS:
            changed = (
                _write_quicktime_atom_creation_time(file, payload_start, atom_end, seconds)
                or changed
            )
        elif atom_type in QUICKTIME_CONTAINER_ATOMS:
            child_start = payload_start + (4 if atom_type == b"meta" else 0)
            changed = (
                _write_quicktime_timestamp_in_range(file, child_start, atom_end, seconds) or changed
            )

        offset = atom_end

    return changed


# Write one QuickTime atom's creation-time field
def _write_quicktime_atom_creation_time(file: Any, start: int, end: int, seconds: int) -> bool:
    file.seek(start)
    version_data = file.read(1)
    if len(version_data) != 1:
        return False

    version = version_data[0]
    creation_time_offset = start + 4

    if version == 1:
        if creation_time_offset + 8 > end:
            return False
        file.seek(creation_time_offset)
        file.write(struct.pack(">Q", seconds))
        return True

    if creation_time_offset + 4 > end:
        return False

    if seconds > 0xFFFFFFFF:
        raise ValueError("QuickTime version 0 metadata cannot store timestamps after 2040-02-06")

    file.seek(creation_time_offset)
    file.write(struct.pack(">I", seconds))
    return True
