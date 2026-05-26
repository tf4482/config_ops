"""Run named SSH command sets from project config.

The script selects an ``ssh`` set from ``config.yaml``, validates connection and
command settings, executes ``ssh.exe``, and reports common SSH failures clearly.
"""

import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from winutils_python import menu, visual

CONFIG_SECTION = "ssh"
CANCEL_CHOICES = {"exit", "quit", "cancel"}


DEFAULT_SECTION = r'''ssh:
  example_set:
    user: 'user'
    host: '192.168.1.1'
    port: 22
    timeout: 300
    command: 'ls'
'''


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


def load_config(script_file: str | Path) -> dict[str, Any]:
    """Load ``config.yaml`` and attach its path under an internal helper key."""

    path = find_config_path(script_file)
    loaded_config = parse_yaml(path.read_text(encoding="utf-8"))

    if loaded_config is None:
        loaded_config = {}

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


def parse_yaml(config_text: str) -> dict[str, Any]:
    """Parse YAML config text into a dictionary, treating empty files as empty."""

    return yaml.safe_load(config_text) or {}


def get_table(config: dict[str, Any], name: str) -> dict[str, Any]:
    """Return a top-level config table or raise when the value is not a table."""

    value = config.get(name, {})

    if not isinstance(value, dict):
        raise TypeError(f"Configuration value '{name}' must be a table")

    return value


def get_ssh_sets(config: dict[str, Any]) -> dict[str, Any]:
    """Return all configured SSH task sets."""

    return get_table(config, CONFIG_SECTION)


def choose_ssh_set_terminal(config: dict[str, Any]) -> str:
    """Prompt the user to choose an SSH set from the terminal."""

    ssh_sets = get_ssh_sets(config)
    return menu.choose_mapping_key_terminal(
        ssh_sets,
        header="Available SSH sets:",
        empty_message=f"No SSH sets configured in config.yaml. Please add an '{CONFIG_SECTION}' section.",
        prompt="Select SSH set by number or name (or 'exit' to cancel): ",
    )


def validate_ssh_set_name(config: dict[str, Any], set_name: str) -> str:
    """Validate a requested SSH set name and handle cancel aliases."""

    if set_name.lower() in CANCEL_CHOICES:
        raise SystemExit(0)

    ssh_sets = get_ssh_sets(config)

    if set_name not in ssh_sets:
        available_sets = ", ".join(ssh_sets) or "none"
        raise SystemExit(f"Unknown SSH set '{set_name}'. Available sets: {available_sets}")

    return set_name


def ssh_set_name(config: dict[str, Any]) -> str:
    """Return the selected SSH set from CLI args or the terminal menu."""

    if len(sys.argv) > 1:
        return validate_ssh_set_name(config, menu.normalize_selection_name(sys.argv[1]))

    return choose_ssh_set_terminal(config)


def get_ssh_set(config: dict[str, Any], set_name: str) -> dict[str, Any]:
    """Return the configuration table for a named SSH set."""

    if set_name.lower() in CANCEL_CHOICES:
        raise SystemExit(0)

    ssh_sets = get_ssh_sets(config)
    ssh_cfg = ssh_sets.get(set_name)

    if not isinstance(ssh_cfg, dict):
        raise ValueError(f"SSH set '{set_name}' was not found in config.yaml")

    return ssh_cfg


def required_str(config: dict[str, Any], key: str) -> str:
    """Return a required non-empty string config value."""

    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"SSH config value '{key}' must be a non-empty string")

    return value


def required_port(config: dict[str, Any]) -> int:
    """Return and validate the configured SSH port."""

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


def optional_timeout(config: dict[str, Any]) -> float | None:
    """Return the optional positive SSH timeout in seconds."""

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


def ensure_section(config: dict[str, Any]) -> None:
    """Ensure the SSH section exists and has table shape."""

    if CONFIG_SECTION not in config:
        append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning(
            f"Added default '{CONFIG_SECTION}' section to config.yaml. Please configure it before running."
        )
        raise SystemExit(1)

    get_ssh_sets(config)


def report_ssh_error(title: str, message: str) -> None:
    """Print a formatted SSH error message."""

    visual.print_error(f"{title}: {message}")


def run_ssh_task(
    set_name: str,
    user: str,
    host: str,
    port: int,
    command: str,
    timeout: float | None,
) -> None:
    """Run one SSH command and translate common failures into clear messages."""

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
    """Run the selected SSH task set."""

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
