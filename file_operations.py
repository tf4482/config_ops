import sys
from pathlib import Path
from typing import Any

import yaml

from winutils_python import connect_smb, file_ops, menu, visual


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


def load(script_file: str | Path) -> dict[str, Any]:
    path = find_config_path(script_file)
    loaded_config = parse_yaml(path.read_text(encoding="utf-8")) or {}
    loaded_config["__config_path__"] = path
    return loaded_config


def append_section_yaml(config: dict[str, Any], section_yaml: str) -> None:
    path = config.get("__config_path__")

    if isinstance(path, Path):
        existing = path.read_text(encoding="utf-8").rstrip()
        separator = "\n\n" if existing else ""
        path.write_text(existing + separator + section_yaml.strip() + "\n", encoding="utf-8")


def get_table(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name, {})

    if not isinstance(value, dict):
        raise TypeError(f"Configuration value '{name}' must be a table")

    return value


class config_loader:
    load = staticmethod(load)
    append_section_yaml = staticmethod(append_section_yaml)
    get_table = staticmethod(get_table)


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


def validate_operation_set_name(config: dict, set_name: str) -> str:
    operation_sets = config_loader.get_table(config, "file_operations")

    if set_name not in operation_sets:
        available_sets = ", ".join(operation_sets) or "none"
        raise SystemExit(f"Unknown file operation set '{set_name}'. Available sets: {available_sets}")

    return set_name


def operation_set_config(config: dict, set_name: str) -> dict:
    operation_sets = config_loader.get_table(config, "file_operations")
    operation_set = operation_sets.get(set_name)

    if not isinstance(operation_set, dict):
        raise TypeError(f"File operation set '{set_name}' must be a table")

    return operation_set


def choose_operation_set_terminal(config: dict) -> str:
    operation_sets = config_loader.get_table(config, "file_operations")
    return menu.choose_mapping_key_terminal(
        operation_sets,
        header="Available file operation sets:",
        empty_message="No file operation sets configured in config.yaml. Please add a 'file_operations' section.",
    )


def operation_set_name(config: dict) -> str:
    if len(sys.argv) > 1:
        return validate_operation_set_name(config, menu.normalize_selection_name(sys.argv[1]))

    return choose_operation_set_terminal(config)


def ensure_section(config: dict) -> None:
    if "file_operations" not in config:
        config_loader.append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning("Added default 'file_operations' section to config.yaml. Please configure it before running.")
        raise SystemExit(1)


def config_for_operation_set_smb(config: dict, set_name: str) -> dict:
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


def store_prompted_smb_password(config: dict, set_name: str, password: str) -> None:
    operation_set = operation_set_config(config, set_name)
    set_smb = operation_set.get("smb", False)

    if set_smb is True:
        config_path = config.get("__config_path__")
        if config_path is None:
            raise ValueError("Loaded configuration is missing internal '__config_path__'")

        connect_smb.replace_or_add_string_value(config_path, "smb", "encrypted_password", connect_smb.encrypt_password(password))
        connect_smb.remove_value(config_path, "smb", "password_file")
        connect_smb.remove_value(config_path, "smb", "password")


def main() -> None:
    config = config_loader.load(__file__)
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
