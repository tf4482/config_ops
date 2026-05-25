import subprocess
import sys
import winreg
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from winutils_python import visual


VALID_COMMANDS = {"on", "off", "toggle", "suspend", "resume"}
DEFAULT_COMMAND = "toggle"

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
    name: str
    on_url: str
    off_url: str


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


def normalize_argument(argument: str) -> str:
    return argument.strip().lower().lstrip("/-")


def ensure_section(config: dict[str, Any]) -> None:
    if "peripherals" not in config:
        append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning(
            "Added default 'peripherals' section to config.yaml. "
            "Please configure the device URLs before running."
        )
        raise SystemExit(1)


def peripherals_config(config: dict[str, Any]) -> dict[str, Any]:
    return get_table(config, "peripherals")


def registry_path_from_config(peripherals: dict[str, Any]) -> str:
    registry_path = str(peripherals.get("registry_path", "")).strip()

    if not registry_path:
        raise ValueError("Configuration value 'peripherals.registry_path' must be set")

    return registry_path


def devices_from_config(peripherals: dict[str, Any]) -> dict[str, PeripheralDevice]:
    devices: dict[str, PeripheralDevice] = {}

    for name, value in peripherals.items():
        if name == "registry_path":
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
        devices[normalized_name] = PeripheralDevice(name=normalized_name, on_url=on_url, off_url=off_url)

    if not devices:
        raise ValueError("No peripheral devices configured")

    return devices


def read_device_state(registry_path: str, device: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
            value, _ = winreg.QueryValueEx(key, device)
            return bool(value)
    except FileNotFoundError:
        return False


def write_device_state(registry_path: str, device: str, enabled: bool) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
        winreg.SetValueEx(key, device, 0, winreg.REG_DWORD, 1 if enabled else 0)


def trigger_url(url: str) -> None:
    subprocess.Popen(
        ["curl.exe", url],
        cwd=script_dir(__file__),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def turn_device_on(registry_path: str, device: PeripheralDevice) -> None:
    write_device_state(registry_path, device.name, True)
    trigger_url(device.on_url)


def turn_device_off(registry_path: str, device: PeripheralDevice) -> None:
    write_device_state(registry_path, device.name, False)
    trigger_url(device.off_url)


def toggle_device(registry_path: str, device: PeripheralDevice) -> None:
    if read_device_state(registry_path, device.name):
        turn_device_off(registry_path, device)
        return

    turn_device_on(registry_path, device)


def suspend_device(device: PeripheralDevice) -> None:
    trigger_url(device.off_url)


def resume_device(registry_path: str, device: PeripheralDevice) -> None:
    if read_device_state(registry_path, device.name):
        trigger_url(device.on_url)
        return

    trigger_url(device.off_url)


def run_device_command(registry_path: str, device: PeripheralDevice, command: str) -> None:
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


def parse_arguments(arguments: list[str], devices: dict[str, PeripheralDevice]) -> tuple[list[PeripheralDevice], str]:
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

    return ([devices[name] for name in selected_device_names], command)


def main() -> None:
    config = load_config(__file__)
    ensure_section(config)

    peripherals = peripherals_config(config)
    registry_path = registry_path_from_config(peripherals)
    devices = devices_from_config(peripherals)

    arguments = [normalize_argument(argument) for argument in sys.argv[1:]]
    selected_devices, command = parse_arguments(arguments, devices)

    for device in selected_devices:
        run_device_command(registry_path, device, command)

    visual.print_done(
        f"Peripheral command finished: /{command} for {len(selected_devices)} device(s)"
    )


if __name__ == "__main__":
    main()
