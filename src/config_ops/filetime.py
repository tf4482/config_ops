from __future__ import annotations

from ctypes import Structure, WinDLL, byref, c_bool, c_uint32, c_void_p, c_wchar_p
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path

INVALID_HANDLE_VALUE = c_void_p(-1).value
WINDOWS_TICK = 10_000_000
WINDOWS_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)
FILE_READ_ATTRIBUTES = 0x0080
FILE_WRITE_ATTRIBUTES = 0x0100
FILE_SHARE_READ_WRITE_DELETE = 0x00000001 | 0x00000002 | 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000


# ctypes representation of the Win32 FILETIME structure
class FileTime(Structure):
    _fields_ = (("dwLowDateTime", c_uint32), ("dwHighDateTime", c_uint32))


# Return raw creation, access, and modification FILETIME values
def get_file_times(path: Path) -> tuple[FileTime, FileTime, FileTime]:
    handle = _open_file_time_handle(path, FILE_READ_ATTRIBUTES)
    creation_time = FileTime()
    access_time = FileTime()
    modification_time = FileTime()

    try:
        success = _kernel32().GetFileTime(
            handle, byref(creation_time), byref(access_time), byref(modification_time)
        )
        if not success:
            raise OSError(f"Could not read timestamps: {path}")
    finally:
        _kernel32().CloseHandle(handle)

    return (
        FileTime(creation_time.dwLowDateTime, creation_time.dwHighDateTime),
        FileTime(access_time.dwLowDateTime, access_time.dwHighDateTime),
        FileTime(modification_time.dwLowDateTime, modification_time.dwHighDateTime),
    )


# Set raw creation, access, and modification FILETIME values
def set_raw_file_times(
    path: Path, creation_time: FileTime, access_time: FileTime, modification_time: FileTime
) -> None:
    handle = _open_file_time_handle(path, FILE_WRITE_ATTRIBUTES)

    try:
        success = _kernel32().SetFileTime(
            handle, byref(creation_time), byref(access_time), byref(modification_time)
        )
        if not success:
            raise OSError(f"Could not set timestamps: {path}")
    finally:
        _kernel32().CloseHandle(handle)


# Set creation and modification times for a Windows file
def set_creation_and_modification_times(path: Path, timestamp: datetime) -> None:
    low_date_time, high_date_time = _datetime_to_filetime(timestamp)
    filetime = FileTime(low_date_time, high_date_time)
    set_raw_file_times(path, filetime, filetime, filetime)


# Windows modification time of a file as local datetime
def modification_time(path: Path) -> datetime:
    return _filetime_to_datetime(get_file_times(path)[2])


# Kernel32 with typed file time functions, loaded on first use
@cache
def _kernel32() -> WinDLL:
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
    kernel32.GetFileTime.argtypes = (c_void_p, c_void_p, c_void_p, c_void_p)
    kernel32.GetFileTime.restype = c_bool
    kernel32.SetFileTime.argtypes = (c_void_p, c_void_p, c_void_p, c_void_p)
    kernel32.SetFileTime.restype = c_bool
    kernel32.CloseHandle.argtypes = (c_void_p,)
    kernel32.CloseHandle.restype = c_bool
    return kernel32


# Convert a Python datetime into low/high Windows FILETIME integers
def _datetime_to_filetime(timestamp: datetime) -> tuple[int, int]:
    utc_timestamp = timestamp.astimezone(UTC)
    ticks = int((utc_timestamp - WINDOWS_EPOCH).total_seconds() * WINDOWS_TICK)
    return ticks & 0xFFFFFFFF, ticks >> 32


# Convert a Win32 FILETIME structure into a local Python datetime
def _filetime_to_datetime(filetime: FileTime) -> datetime:
    ticks = filetime.dwLowDateTime + (filetime.dwHighDateTime << 32)
    return (WINDOWS_EPOCH + timedelta(seconds=ticks / WINDOWS_TICK)).astimezone()


# Open a Windows file handle suitable for reading or writing timestamps
def _open_file_time_handle(path: Path, access: int) -> c_void_p:
    handle = _kernel32().CreateFileW(
        str(path),
        access,
        FILE_SHARE_READ_WRITE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )

    if handle == INVALID_HANDLE_VALUE:
        raise OSError(f"Could not open file for timestamp access: {path}")

    return handle
