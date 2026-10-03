"""Run named Robocopy-based file operation sets from project config.

The script selects a ``file_operations`` set, optionally connects SMB mappings,
and delegates mirror/copy/move/sync execution to ``winutils_python.file_ops``.
"""

from pathlib import Path
from typing import Any

from winutils_python import config as config_utils
from winutils_python import (
    config_sets,
    config_validation,
    connect_smb,
    file_ops,
    visual,
)

CONFIG_SECTION = "file_operations"

DEFAULT_SECTION = r"""file_operations:
  example_set:
    smb: false
    robocopy:
      common_options: ['/MT:32', '/W:2', '/R:10', '/XJD', '/XJF', '/XJ', '/XC', '/NFL', '/ETA', '/TEE']
    mirror:
      - source: 'C:\path\to\source'
        target: 'R:\path\to\target'
    copy:
      overwrite: false
      options: ['/E', '/MT:16', '/W:2', '/R:5', '/XJD', '/XJF', '/XJ', '/XC', '/NFL', '/ETA', '/TEE']
      tasks:
        - source: 'C:\path\to\source'
          target: 'E:\path\to\target'
    move:
      overwrite: true
      tasks:
        - source: 'C:\path\to\source'
          target: 'R:\path\to\target'
    sync:
      tasks:
        - source: 'C:\path\to\first-folder'
          target: 'R:\path\to\second-folder'
"""


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


def validate_operation_set(operation_set: dict[str, Any], set_name: str) -> None:
    """Validate operation groups and required task values before execution."""

    for operation_type in file_ops.OPERATION_TYPES:
        operation_group = operation_set.get(operation_type, [])
        tasks = file_ops.operation_group_tasks(operation_group)
        default_overwrite = file_ops.operation_group_overwrite(operation_group)
        default_options = file_ops.operation_set_robocopy_options(operation_set, operation_type)
        file_ops.operation_group_options(operation_group, operation_type, default_options)
        config_validation.require_list_item_keys(
            tasks,
            config_sets.config_key(CONFIG_SECTION, set_name, operation_type),
            (
                config_validation.required_key("source"),
                config_validation.required_key("target"),
            ),
        )

        for task in tasks:
            config_utils.optional_bool(
                task,
                "overwrite",
                label=config_sets.config_key(CONFIG_SECTION, set_name, f"{operation_type}.overwrite"),
                default=default_overwrite,
            )


def prepare_sync_directories(operation_set: dict[str, Any]) -> None:
    """Missing sync root preparation."""

    for task in file_ops.operation_group_tasks(operation_set.get("sync", [])):
        source = Path(str(task["source"])).resolve(strict=False)
        target = Path(str(task["target"])).resolve(strict=False)

        # Reject overlapping roots before creating directories
        if source == target:
            raise ValueError(f"Sync source and target must be different folders: {source}")
        if source in target.parents or target in source.parents:
            raise ValueError(f"Sync folders must not contain one another: {source} ↔ {target}")

        for label, path in (("source", source), ("target", target)):
            if path.exists() and not path.is_dir():
                raise NotADirectoryError(f"Sync {label} is not a folder: {path}")

        # Create both roots for the forward and reverse Robocopy passes
        source.mkdir(parents=True, exist_ok=True)
        target.mkdir(parents=True, exist_ok=True)


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
    operation_set = operation_set_config(config, set_name)
    validate_operation_set(operation_set, set_name)
    connect_smb.connect_from_config(
        connect_smb.scoped_config_for_optional_smb(
            config,
            operation_set,
            error_label=f"File operation set '{set_name}' value 'smb'",
        ),
        on_password_prompted=lambda password: connect_smb.store_prompted_password_if_enabled(
            config,
            operation_set,
            password,
            error_label=f"File operation set '{set_name}' value 'smb'",
        ),
    )
    prepare_sync_directories(operation_set)
    file_ops.run_operation_set(config, set_name)
    visual.print_done(f"File operations finished: {set_name}")


if __name__ == "__main__":
    main()
