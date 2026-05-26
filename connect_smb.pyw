"""Connect SMB network shares defined in the project config file.

This GUI-friendly entry script loads the top-level ``smb`` configuration,
delegates mapping work to ``winutils_python.connect_smb``, and persists prompted
passwords in obfuscated form for later runs.
"""

import sys
from pathlib import Path
from typing import Any

import yaml

from winutils_python import connect_smb, visual

CONFIG_SECTION = "smb"


DEFAULT_SECTION = r'''smb:
  user: 'DOMAIN\user'
  mappings:
    - drive: 'Q:'
      share: '\\SERVER\backup'
    - drive: 'R:'
      share: '\\SERVER\data'
    - drive: 'S:'
      share: '\\SERVER\develop'
'''


def script_dir(script_file: str | Path) -> Path:
    """Return the directory containing the script or frozen executable."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(script_file).resolve().parent


def config_path(script_file: str | Path) -> Path:
    """Return the expected ``config.yaml`` path for this script."""

    return script_dir(script_file) / "config.yaml"


def find_config_path(script_file: str | Path) -> Path:
    """Return the config path, creating an empty config file when missing."""

    path = config_path(script_file)

    if not path.exists():
        path.write_text("", encoding="utf-8")

    return path


def load(script_file: str | Path) -> dict[str, Any]:
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


def replace_or_add_string_value(config_path: Path, table: str, key: str, value: str) -> None:
    """Replace or add a string value inside a top-level config table."""

    loaded_config = parse_yaml(config_path.read_text(encoding="utf-8")) or {}
    table_config = loaded_config.setdefault(table, {})

    if not isinstance(table_config, dict):
        raise TypeError(f"Configuration value '{table}' must be a table")

    table_config[key] = value
    config_path.write_text(dump_yaml(loaded_config), encoding="utf-8")


def remove_value(config_path: Path, table: str, key: str) -> None:
    """Remove a key from a top-level config table when it exists."""

    loaded_config = parse_yaml(config_path.read_text(encoding="utf-8")) or {}
    table_config = loaded_config.get(table, {})

    if isinstance(table_config, dict) and key in table_config:
        del table_config[key]
        config_path.write_text(dump_yaml(loaded_config), encoding="utf-8")


def parse_yaml(config_text: str) -> dict[str, Any]:
    """Parse YAML config text into a dictionary, treating empty files as empty."""

    return yaml.safe_load(config_text) or {}


def dump_yaml(config: dict[str, Any]) -> str:
    """Serialize config while omitting internal helper keys."""

    clean = {key: value for key, value in config.items() if not key.startswith("__")}
    return yaml.safe_dump(clean, sort_keys=False, allow_unicode=True)


def get_table(config: dict[str, Any], name: str) -> dict[str, Any]:
    """Return a top-level config table or raise when the value is not a table."""

    value = config.get(name, {})

    if not isinstance(value, dict):
        raise TypeError(f"Configuration value '{name}' must be a table")

    return value


def ensure_section(config: dict[str, Any]) -> None:
    """Ensure the SMB section exists and has table shape."""

    if CONFIG_SECTION not in config:
        append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning(
            f"Added default '{CONFIG_SECTION}' section to config.yaml. "
            "Please configure 'smb.user' and 'smb.mappings' before running. "
            "The encrypted password will be added after the first successful password prompt."
        )
        raise SystemExit(1)

    get_table(config, CONFIG_SECTION)


def store_prompted_smb_password(config: dict[str, Any], password: str) -> None:
    """Persist a prompted SMB password and remove legacy password fields."""

    config_path = config.get("__config_path__")

    if config_path is None:
        raise ValueError("Loaded configuration is missing internal '__config_path__'")

    replace_or_add_string_value(
        config_path,
        CONFIG_SECTION,
        "encrypted_password",
        connect_smb.encrypt_password(password),
    )
    remove_value(config_path, CONFIG_SECTION, "password_file")
    remove_value(config_path, CONFIG_SECTION, "password")


def main() -> None:
    """Connect all configured top-level SMB mappings."""

    config = load(__file__)
    ensure_section(config)
    results = connect_smb.connect_from_config(
        config,
        on_password_prompted=lambda password: store_prompted_smb_password(config, password),
    )

    if results:
        visual.print_done(f"SMB shares connected: {len(results)} mapping(s)")
    else:
        visual.print_warning("No SMB shares connected")


if __name__ == "__main__":
    main()
