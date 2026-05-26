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


def main() -> None:
    """Connect all configured top-level SMB mappings."""

    config = load(__file__)
    ensure_section(config)
    results = connect_smb.connect_from_config(
        config,
        on_password_prompted=lambda password: connect_smb.store_prompted_password(config, password),
    )

    if results:
        visual.print_done(f"SMB shares connected: {len(results)} mapping(s)")
    else:
        visual.print_warning("No SMB shares connected")


if __name__ == "__main__":
    main()
