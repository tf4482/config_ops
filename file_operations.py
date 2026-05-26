"""Run named Robocopy-based file operation sets from project config.

The script selects a ``file_operations`` set, optionally connects SMB mappings,
and delegates mirror/copy/move execution to ``winutils_python.file_ops``.
"""

import sys
from typing import Any

from winutils_python import config as config_utils
from winutils_python import connect_smb, file_ops, menu, visual

CONFIG_SECTION = "file_operations"

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

    return config_utils.get_table(config, CONFIG_SECTION)


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
        config_utils.append_section_yaml(config, DEFAULT_SECTION)
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

    config = config_utils.load(__file__)
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
