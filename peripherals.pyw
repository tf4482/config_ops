"""Control URL-backed peripherals and persist their state in the registry.

The script reads configured devices from ``config.yaml``, normalizes command-line
arguments, stores device state under ``HKEY_CURRENT_USER``, and triggers device
URLs with ``curl.exe`` without opening terminal windows.
"""

import subprocess
import sys
import winreg
from dataclasses import dataclass
from typing import Any

from winutils_python import config as config_utils
from winutils_python import config_validation
from winutils_python import visual


CONFIG_SECTION = "peripherals"
REGISTRY_PATH_KEY = "registry_path"
VALID_COMMANDS = {"on", "off", "toggle", "suspend", "resume"}
DEFAULT_COMMAND = "toggle"
CREATE_NO_WINDOW = 0x08000000

DEFAULT_SECTION = r'''peripherals:
  registry_path: 'Software\peripherals'

  led:
    on: 'https://www.placeholder'
    off: 'https://www.placeholder'

  tv:
    on: 'https://www.placeholder'
    off: 'https://www.placeholder'
'''


@dataclass(frozen=True)
class PeripheralDevice:
    """A configured peripheral with separate on/off trigger URLs."""

    name: str
    on_url: str
    off_url: str

def normalize_argument(argument: str) -> str:
    """Normalize CLI device and command arguments."""

    return argument.strip().lower().lstrip("/-")


def ensure_section(config: dict[str, Any]) -> None:
    """Ensure the peripherals section exists and has table shape."""

    if CONFIG_SECTION not in config:
        config_utils.append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning(
            f"Added default '{CONFIG_SECTION}' section to config.yaml. "
            "Please configure the device URLs before running."
        )
        raise SystemExit(1)

    peripherals_config(config)


def peripherals_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return the top-level peripherals configuration table."""

    return config_utils.get_table(config, CONFIG_SECTION)


def config_key(name: str) -> str:
    """Return a dotted config key for human-readable error messages."""

    return f"{CONFIG_SECTION}.{name}"


def registry_path_from_config(peripherals: dict[str, Any]) -> str:
    """Return the registry path used to persist peripheral state."""

    registry_path = str(peripherals.get(REGISTRY_PATH_KEY, "")).strip()

    if not registry_path:
        raise ValueError(f"Configuration value '{config_key(REGISTRY_PATH_KEY)}' must be set")

    return registry_path


def validate_peripherals_config(peripherals: dict[str, Any]) -> None:
    """Report missing required peripheral configuration."""

    config_validation.require_keys(
        peripherals,
        (config_validation.required_key(REGISTRY_PATH_KEY, label=config_key(REGISTRY_PATH_KEY)),),
    )

    for name, value in peripherals.items():
        if name == REGISTRY_PATH_KEY:
            continue

        if not isinstance(value, dict):
            visual.print_error("Missing required configuration option(s):")
            visual.print_error(f"- {config_key(name)} must be a table")
            raise SystemExit(1)

        missing_keys: list[str] = []
        if not str(value.get("on", value.get(True, ""))).strip():
            missing_keys.append(f"{config_key(name)}.on")
        if not str(value.get("off", value.get(False, ""))).strip():
            missing_keys.append(f"{config_key(name)}.off")

        config_validation.report_missing_keys(missing_keys)
        if missing_keys:
            raise SystemExit(1)


def devices_from_config(peripherals: dict[str, Any]) -> dict[str, PeripheralDevice]:
    """Build configured peripheral devices keyed by normalized device name."""

    devices: dict[str, PeripheralDevice] = {}

    for name, value in peripherals.items():
        if name == REGISTRY_PATH_KEY:
            continue

        if not isinstance(value, dict):
            raise TypeError(f"Peripheral device '{name}' must be a table")

        on_url = str(value.get("on", value.get(True, ""))).strip()
        off_url = str(value.get("off", value.get(False, ""))).strip()

        if not on_url:
            raise ValueError(f"Peripheral device '{name}' must define an 'on' URL")

        if not off_url:
            raise ValueError(f"Peripheral device '{name}' must define an 'off' URL")

        normalized_name = normalize_argument(name)
        devices[normalized_name] = PeripheralDevice(
            name=normalized_name,
            on_url=on_url,
            off_url=off_url,
        )

    if not devices:
        raise ValueError("No peripheral devices configured")

    return devices


def read_device_state(registry_path: str, device: str) -> bool:
    """Read a peripheral state value from ``HKEY_CURRENT_USER``."""

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
            value, _ = winreg.QueryValueEx(key, device)
            return bool(value)
    except FileNotFoundError:
        return False


def write_device_state(registry_path: str, device: str, enabled: bool) -> None:
    """Persist a peripheral state value under ``HKEY_CURRENT_USER``."""

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
        winreg.SetValueEx(key, device, 0, winreg.REG_DWORD, 1 if enabled else 0)


def subprocess_creationflags() -> int:
    """Return Windows no-console creation flags for background URL triggers."""

    if sys.platform == "win32":
        return CREATE_NO_WINDOW

    return 0


def trigger_url(url: str) -> None:
    """Trigger a device URL with ``curl.exe`` in the background."""

    subprocess.Popen(
        ["curl.exe", url],
        cwd=config_utils.script_dir(__file__),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess_creationflags(),
    )


def turn_device_on(registry_path: str, device: PeripheralDevice) -> None:
    """Persist a device as enabled and trigger its on URL."""

    write_device_state(registry_path, device.name, True)
    trigger_url(device.on_url)


def turn_device_off(registry_path: str, device: PeripheralDevice) -> None:
    """Persist a device as disabled and trigger its off URL."""

    write_device_state(registry_path, device.name, False)
    trigger_url(device.off_url)


def toggle_device(registry_path: str, device: PeripheralDevice) -> None:
    """Toggle a device based on its persisted state."""

    if read_device_state(registry_path, device.name):
        turn_device_off(registry_path, device)
        return

    turn_device_on(registry_path, device)


def suspend_device(device: PeripheralDevice) -> None:
    """Trigger a device off without changing persisted state."""

    trigger_url(device.off_url)


def resume_device(registry_path: str, device: PeripheralDevice) -> None:
    """Trigger the URL matching the device's persisted state."""

    if read_device_state(registry_path, device.name):
        trigger_url(device.on_url)
        return

    trigger_url(device.off_url)


def run_device_command(registry_path: str, device: PeripheralDevice, command: str) -> None:
    """Run one supported command for one peripheral device."""

    if command == "on":
        turn_device_on(registry_path, device)
    elif command == "off":
        turn_device_off(registry_path, device)
    elif command == "toggle":
        toggle_device(registry_path, device)
    elif command == "suspend":
        suspend_device(device)
    elif command == "resume":
        resume_device(registry_path, device)
    else:
        raise ValueError(f"Unsupported command: {command}")


def parse_arguments(
    arguments: list[str],
    devices: dict[str, PeripheralDevice],
) -> tuple[list[PeripheralDevice], str]:
    """Parse normalized CLI arguments into selected devices and command."""

    selected_device_names: list[str] = []
    command = DEFAULT_COMMAND

    for argument in arguments:
        if argument in devices:
            selected_device_names.append(argument)
            continue

        if argument in VALID_COMMANDS:
            command = argument
            continue

        valid_devices = ", ".join(sorted(devices))
        valid_commands = ", ".join(sorted(VALID_COMMANDS))

        raise ValueError(
            f"Invalid argument: /{argument}. "
            f"Valid devices: {valid_devices}. "
            f"Valid commands: {valid_commands}."
        )

    if not selected_device_names:
        selected_device_names = list(devices.keys())

    selected_devices = [devices[name] for name in selected_device_names]
    return selected_devices, command


def main() -> None:
    """Run the requested peripheral command for selected devices."""

    config = config_utils.load(__file__)
    ensure_section(config)

    peripherals = peripherals_config(config)
    validate_peripherals_config(peripherals)
    registry_path = registry_path_from_config(peripherals)
    devices = devices_from_config(peripherals)

    arguments = [normalize_argument(argument) for argument in sys.argv[1:]]
    selected_devices, command = parse_arguments(arguments, devices)

    for device in selected_devices:
        run_device_command(registry_path, device, command)

    visual.print_done(f"Peripheral command finished: /{command} for {len(selected_devices)} device(s)")


if __name__ == "__main__":
    main()
