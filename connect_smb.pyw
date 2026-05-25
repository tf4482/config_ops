import sys
from pathlib import Path
from typing import Any

import yaml

from winutils_python import connect_smb, visual


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
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(script_file).resolve().parent


def config_path(script_file: str | Path) -> Path:
    return script_dir(script_file) / "config.yaml"


def find_config_path(script_file: str | Path) -> Path:
    path = config_path(script_file)

    if not path.exists():
        path.write_text("", encoding="utf-8")

    return path


def load(script_file: str | Path) -> dict[str, Any]:
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


def ensure_section(config: dict) -> None:
    if "smb" not in config:
        append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning(
            "Added default 'smb' section to config.yaml. "
            "Please configure 'smb.user' and 'smb.mappings' before running. "
            "The encrypted password will be added after the first successful password prompt."
        )
        raise SystemExit(1)


def store_prompted_smb_password(config: dict, password: str) -> None:
    config_path = config.get("__config_path__")
    if config_path is None:
        raise ValueError("Loaded configuration is missing internal '__config_path__'")

    replace_or_add_string_value(config_path, "smb", "encrypted_password", connect_smb.encrypt_password(password))
    remove_value(config_path, "smb", "password_file")
    remove_value(config_path, "smb", "password")


def main() -> None:
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
