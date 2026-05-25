import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from winutils_python import visual


def app_dir(script_file: str | Path) -> Path:
    """Return the directory that should contain config.yaml.

    In a normal Python run this is the script directory.
    In a PyInstaller .exe this is the .exe directory, not the temporary
    extraction directory used by --onefile builds.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(script_file).resolve().parent


def script_dir(script_file: str | Path) -> Path:
    # Backwards-compatible name used by the rest of the script.
    return app_dir(script_file)


def config_path(script_file: str | Path) -> Path:
    return script_dir(script_file) / "config.yaml"


def find_config_path(script_file: str | Path) -> Path:
    path = config_path(script_file)

    if not path.exists():
        path.write_text("", encoding="utf-8")

    return path


def load_config(script_file: str | Path) -> dict[str, Any]:
    path = find_config_path(script_file)
    loaded_config = parse_yaml(path.read_text(encoding="utf-8"))

    if loaded_config is None:
        loaded_config = {}

    loaded_config["__config_path__"] = path
    return loaded_config


def append_section_yaml(config: dict[str, Any], section_yaml: str) -> None:
    path = config.get("__config_path__")
    if isinstance(path, Path):
        existing = path.read_text(encoding="utf-8").rstrip()
        separator = "\n\n" if existing else ""
        path.write_text(existing + separator + section_yaml.strip() + "\n", encoding="utf-8")


def replace_or_add_string_value(config_path: Path, table: str, key: str, value: str) -> None:
    loaded_config = parse_yaml(config_path.read_text(encoding="utf-8")) or {}
    table_config = loaded_config.setdefault(table, {})

    if not isinstance(table_config, dict):
        raise TypeError(f"Configuration value '{table}' must be a table")

    table_config[key] = value
    config_path.write_text(dump_yaml(loaded_config), encoding="utf-8")


def remove_value(config_path: Path, table: str, key: str) -> None:
    loaded_config = parse_yaml(config_path.read_text(encoding="utf-8")) or {}
    table_config = loaded_config.get(table, {})

    if isinstance(table_config, dict) and key in table_config:
        del table_config[key]
        config_path.write_text(dump_yaml(loaded_config), encoding="utf-8")


def parse_yaml(config_text: str) -> dict[str, Any]:
    return yaml.safe_load(config_text) or {}


def dump_yaml(config: dict[str, Any]) -> str:
    clean = {k: v for k, v in config.items() if not k.startswith("__")}
    return yaml.safe_dump(clean, sort_keys=False, allow_unicode=True)


def get_table(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name, {})

    if not isinstance(value, dict):
        raise TypeError(f"Configuration value '{name}' must be a table")

    return value


DEFAULT_SECTION = r'''ssh:
  example_set:
    user: 'user'
    host: '192.168.1.1'
    port: 22
    timeout: 300
    command: 'ls'
'''


def normalize_set_name(set_name: str) -> str:
    return set_name.lstrip("/-")


def choose_ssh_set_terminal(config: dict) -> str:
    ssh_sets = get_table(config, "ssh")

    if not ssh_sets:
        raise SystemExit("No SSH sets configured in config.yaml. Please add an 'ssh' section.")

    names = list(ssh_sets.keys())
    visual.print_list_header("Available SSH sets:")
    for index, name in enumerate(names, start=1):
        visual.print_list_item(index, name)

    while True:
        choice = input("Select SSH set by number or name (or 'exit' to cancel): ").strip()

        if not choice or choice.lower() in {"exit", "quit", "cancel"}:
            raise SystemExit(0)

        normalized = normalize_set_name(choice)
        if normalized in ssh_sets:
            return normalized

        if choice.isdigit():
            number = int(choice)
            if 1 <= number <= len(names):
                return names[number - 1]

        visual.print_warning("Invalid selection. Try again.")


def validate_ssh_set_name(config: dict, set_name: str) -> str:
    if set_name.lower() in {"exit", "quit", "cancel"}:
        raise SystemExit(0)

    ssh_sets = get_table(config, "ssh")

    if set_name not in ssh_sets:
        available_sets = ", ".join(ssh_sets) or "none"
        raise SystemExit(f"Unknown SSH set '{set_name}'. Available sets: {available_sets}")

    return set_name


def ssh_set_name(config: dict) -> str:
    if len(sys.argv) > 1:
        return validate_ssh_set_name(config, normalize_set_name(sys.argv[1]))

    return choose_ssh_set_terminal(config)


def get_ssh_set(config: dict, set_name: str) -> dict:
    if set_name.lower() in {"exit", "quit", "cancel"}:
        raise SystemExit(0)

    ssh_sets = get_table(config, "ssh")
    ssh_cfg = ssh_sets.get(set_name)

    if not isinstance(ssh_cfg, dict):
        raise ValueError(f"SSH set '{set_name}' was not found in config.yaml")

    return ssh_cfg


def required_str(config: dict, key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"SSH config value '{key}' must be a non-empty string")

    return value


def required_port(config: dict) -> int:
    value = config.get("port")
    port: int

    if isinstance(value, int):
        port = value
    elif isinstance(value, str) and value.isdigit():
        port = int(value)
    else:
        raise ValueError("SSH config value 'port' must be an integer")

    if not 1 <= port <= 65535:
        raise ValueError("SSH config value 'port' must be in range 1..65535")

    return port


def optional_timeout(config: dict) -> float | None:
    value = config.get("timeout")

    if value is None:
        return None

    if isinstance(value, int | float):
        timeout = float(value)
    elif isinstance(value, str):
        try:
            timeout = float(value)
        except ValueError as error:
            raise ValueError("SSH config value 'timeout' must be a positive number") from error
    else:
        raise ValueError("SSH config value 'timeout' must be a positive number")

    if timeout <= 0:
        raise ValueError("SSH config value 'timeout' must be greater than 0")

    return timeout


def ensure_section(config: dict) -> None:
    if "ssh" not in config:
        append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning("Added default 'ssh' section to config.yaml. Please configure it before running.")
        raise SystemExit(1)


def report_ssh_error(title: str, message: str) -> None:
    visual.print_error(f"{title}: {message}")


def run_ssh_task(set_name: str, user: str, host: str, port: int, command: str, timeout: float | None) -> None:
    try:
        subprocess.run(
            ["ssh", f"{user}@{host}", "-p", str(port), command],
            check=True,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        message = "The 'ssh' executable was not found in PATH. Install OpenSSH or add ssh.exe to PATH."
        report_ssh_error(f"SSH task failed: {set_name}", message)
        raise RuntimeError(message) from error
    except subprocess.TimeoutExpired as error:
        message = f"SSH task timed out after {error.timeout} second(s)."
        report_ssh_error(f"SSH task timed out: {set_name}", message)
        raise
    except subprocess.CalledProcessError as error:
        stderr = (error.stderr or "").strip()
        stdout = (error.stdout or "").strip()
        details = stderr or stdout or "No SSH output was captured."
        message = f"SSH exited with code {error.returncode}. {details}"
        report_ssh_error(f"SSH task failed: {set_name}", message)
        raise


def main() -> None:
    cfg = load_config(__file__)
    ensure_section(cfg)
    set_name = ssh_set_name(cfg)
    ssh_cfg = get_ssh_set(cfg, set_name)

    user = required_str(ssh_cfg, "user")
    host = required_str(ssh_cfg, "host")
    port = required_port(ssh_cfg)
    timeout = optional_timeout(ssh_cfg)
    command = required_str(ssh_cfg, "command")

    visual.print_start(f"Starting SSH task: {set_name}")
    run_ssh_task(set_name, user, host, port, command, timeout)
    visual.print_done(f"SSH task finished: {set_name}")


if __name__ == "__main__":
    main()
