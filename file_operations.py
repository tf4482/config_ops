"""Run named Robocopy-based file operation sets from project config.

The script selects a ``file_operations`` set, optionally connects SMB mappings,
and delegates mirror/copy/move execution to ``winutils_python.file_ops``.
"""

import sys
from pathlib import Path
from typing import Any

import yaml

from winutils_python import connect_smb, file_ops, menu, visual

CONFIG_SECTION = "file_operations"


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


def parse_yaml(config_text: str) -> dict[str, Any]:
    """Parse YAML config text into a dictionary, treating empty files as empty."""

    return yaml.safe_load(config_text) or {}


def dump_yaml(config: dict[str, Any]) -> str:
    """Serialize config while omitting internal helper keys."""

    clean = {key: value for key, value in config.items() if not key.startswith("__")}
    return yaml.safe_dump(clean, sort_keys=False, allow_unicode=True)


def load(script_file: str | Path) -> dict[str, Any]:
    """Load ``config.yaml`` and attach its path under an internal helper key."""

    path = find_config_path(script_file)
    loaded_config = parse_yaml(path.read_text(encoding="utf-8")) or {}
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


def get_table(config: dict[str, Any], name: str) -> dict[str, Any]:
    """Return a top-level config table or raise when the value is not a table."""

    value = config.get(name, {})

    if not isinstance(value, dict):
        raise TypeError(f"Configuration value '{name}' must be a table")

    return value


DEFAULT_SECTION = r'''file_operations:
  example_set:
    smb: false
    robocopy:
      common_options: ['/MT:32', '/W:2', '/R:10', '/XJD', '/XJF', '/XJ', '/XC', '/ETA', '/TEE']
    mirror:
      - source: 'C:\path\to\source'
        target: 'R:\path\to\target'
    copy:
      overwrite: false
      options: ['/E', '/MT:16', '/W:2', '/R:5', '/XJD', '/XJF', '/XJ', '/XC', '/ETA', '/TEE']
      tasks:
        - source: 'C:\path\to\source'
          target: 'E:\path\to\target'
    move:
      overwrite: true
      tasks:
        - source: 'C:\path\to\source'
          target: 'R:\path\to\target'
'''


def get_operation_sets(config: dict[str, Any]) -> dict[str, Any]:
    """Return all configured file operation sets."""

    return get_table(config, CONFIG_SECTION)


def validate_operation_set_name(config: dict[str, Any], set_name: str) -> str:
    """Validate a requested operation set name and return it unchanged."""

    operation_sets = get_operation_sets(config)

    if set_name not in operation_sets:
        available_sets = ", ".join(operation_sets) or "none"
        raise SystemExit(f"Unknown file operation set '{set_name}'. Available sets: {available_sets}")

    return set_name


def operation_set_config(config: dict[str, Any], set_name: str) -> dict[str, Any]:
    """Return the configuration table for a named operation set."""

    operation_sets = get_operation_sets(config)
    operation_set = operation_sets.get(set_name)

    if not isinstance(operation_set, dict):
        raise TypeError(f"File operation set '{set_name}' must be a table")

    return operation_set


def choose_operation_set_terminal(config: dict[str, Any]) -> str:
    """Prompt the user to choose a file operation set from the terminal."""

    operation_sets = get_operation_sets(config)
    return menu.choose_mapping_key_terminal(
        operation_sets,
        header="Available file operation sets:",
        empty_message=f"No file operation sets configured in config.yaml. Please add a '{CONFIG_SECTION}' section.",
    )


def operation_set_name(config: dict[str, Any]) -> str:
    """Return the selected operation set from CLI args or the terminal menu."""

    if len(sys.argv) > 1:
        return validate_operation_set_name(config, menu.normalize_selection_name(sys.argv[1]))

    return choose_operation_set_terminal(config)


def ensure_section(config: dict[str, Any]) -> None:
    """Ensure the file operations section exists and has table shape."""

    if CONFIG_SECTION not in config:
        append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning(
            f"Added default '{CONFIG_SECTION}' section to config.yaml. Please configure it before running."
        )
        raise SystemExit(1)

    get_operation_sets(config)


def config_for_operation_set_smb(config: dict[str, Any], set_name: str) -> dict[str, Any]:
    """Return the scoped config used for optional SMB connection setup."""

    operation_set = operation_set_config(config, set_name)
    set_smb = operation_set.get("smb", False)

    if not isinstance(set_smb, bool):
        raise TypeError(f"File operation set '{set_name}' value 'smb' must be true or false")

    scoped_config = dict(config)

    if set_smb is True:
        if "smb" in config:
            return scoped_config

        scoped_config.pop("smb", None)
        return scoped_config

    if set_smb in (False, None):
        scoped_config.pop("smb", None)
        return scoped_config

    return scoped_config


def store_prompted_smb_password(config: dict[str, Any], set_name: str, password: str) -> None:
    """Persist a prompted SMB password when the selected set uses SMB."""

    operation_set = operation_set_config(config, set_name)
    set_smb = operation_set.get("smb", False)

    if set_smb is True:
        connect_smb.store_prompted_password(config, password)


def main() -> None:
    """Run the selected file operation set."""

    config = load(__file__)
    ensure_section(config)
    set_name = operation_set_name(config)

    visual.print_start(f"Starting file operations: {set_name}")
    connect_smb.connect_from_config(
        config_for_operation_set_smb(config, set_name),
        on_password_prompted=lambda password: store_prompted_smb_password(config, set_name, password),
    )
    file_ops.run_operation_set(config, set_name)
    visual.print_done(f"File operations finished: {set_name}")


if __name__ == "__main__":
    main()
