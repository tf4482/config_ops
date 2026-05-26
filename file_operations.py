"""Run named Robocopy-based file operation sets from project config.

The script selects a ``file_operations`` set, optionally connects SMB mappings,
and delegates mirror/copy/move execution to ``winutils_python.file_ops``.
"""

from typing import Any

from winutils_python import config as config_utils
from winutils_python import config_sets, connect_smb, file_ops, visual

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


def operation_set_config(config: dict[str, Any], set_name: str) -> dict[str, Any]:
    """Return the configuration table for a named operation set."""

    return config_sets.get_set_config(config, CONFIG_SECTION, set_name, label="File operation set")


def ensure_section(config: dict[str, Any]) -> None:
    """Ensure the file operations section exists and has table shape."""

    if CONFIG_SECTION not in config:
        config_utils.append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning(
            f"Added default '{CONFIG_SECTION}' section to config.yaml. Please configure it before running."
        )
        raise SystemExit(1)

    config_sets.section_sets(config, CONFIG_SECTION)


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
    set_name = config_sets.selected_set_name(
        config,
        CONFIG_SECTION,
        label="file operation set",
        header="Available file operation sets:",
        empty_message=f"No file operation sets configured in config.yaml. Please add a '{CONFIG_SECTION}' section.",
    )

    visual.print_start(f"Starting file operations: {set_name}")
    connect_smb.connect_from_config(
        config_for_operation_set_smb(config, set_name),
        on_password_prompted=lambda password: store_prompted_smb_password(config, set_name, password),
    )
    file_ops.run_operation_set(config, set_name)
    visual.print_done(f"File operations finished: {set_name}")


if __name__ == "__main__":
    main()
