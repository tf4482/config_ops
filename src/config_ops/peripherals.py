# URL-controlled devices with their state kept in the current user's registry.
from __future__ import annotations

import subprocess
import winreg
from collections.abc import Sequence

from .config import Device, PeripheralSettings
from .output import GREEN, RED, Printer

COMMANDS = ("on", "off", "toggle", "suspend", "resume")
DEFAULT_COMMAND = "toggle"
CURL_CONNECT_TIMEOUT_SECONDS = 10
CURL_MAX_TIME_SECONDS = 30


# Invalid device or command argument
class PeripheralsError(ValueError):
    pass


# Run one command for the selected devices, or all of them
def run_peripherals(printer: Printer, settings: PeripheralSettings, words: Sequence[str]) -> int:
    devices, command = parse_words(settings, words)
    failed = 0
    for device in devices:
        try:
            run_command(settings.registry_path, device, command)
        except (OSError, subprocess.SubprocessError) as error:
            printer.line("❌", f"{device.name} · {command} · {' '.join(str(error).split())}", RED)
            failed += 1
        else:
            printer.line("✅", f"{device.name} · {command}", GREEN)
    return 1 if failed else 0


# Split words into devices and one command; a leading "/" or "-" is ignored
def parse_words(settings: PeripheralSettings, words: Sequence[str]) -> tuple[list[Device], str]:
    names: list[str] = []
    command = DEFAULT_COMMAND
    for word in words:
        normalized = word.strip().lower().lstrip("/-")
        if normalized in settings.devices:
            names.append(normalized)
        elif normalized in COMMANDS:
            command = normalized
        else:
            raise PeripheralsError(
                f"invalid argument {word}; devices: {', '.join(sorted(settings.devices))}; "
                f"commands: {', '.join(COMMANDS)}"
            )
    selected = names or list(settings.devices)
    return [settings.devices[name] for name in selected], command


# Run one command for one device
def run_command(registry_path: str, device: Device, command: str) -> None:
    match command:
        case "on":
            _switch(registry_path, device, enabled=True)
        case "off":
            _switch(registry_path, device, enabled=False)
        case "toggle":
            _switch(registry_path, device, enabled=not read_state(registry_path, device.name))
        case "suspend":
            trigger_url(device.off_url)
        case "resume":
            enabled = read_state(registry_path, device.name)
            trigger_url(device.on_url if enabled else device.off_url)


# Persisted device state; off when nothing is stored yet
def read_state(registry_path: str, device: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
            value, _ = winreg.QueryValueEx(key, device)
            return bool(value)
    except FileNotFoundError:
        return False


# Persist the device state in the registry
def write_state(registry_path: str, device: str, enabled: bool) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
        winreg.SetValueEx(key, device, 0, winreg.REG_DWORD, 1 if enabled else 0)


# Fail on curl errors and HTTP errors; stderr stays out of the output
def trigger_url(url: str) -> None:
    subprocess.run(
        [
            "curl.exe",
            "--fail-with-body",
            "--silent",
            "--show-error",
            "--connect-timeout",
            str(CURL_CONNECT_TIMEOUT_SECONDS),
            "--max-time",
            str(CURL_MAX_TIME_SECONDS),
            url,
        ],
        check=True,
        capture_output=True,
    )


# Trigger the URL first and persist the state only after success
def _switch(registry_path: str, device: Device, *, enabled: bool) -> None:
    trigger_url(device.on_url if enabled else device.off_url)
    write_state(registry_path, device.name, enabled)
