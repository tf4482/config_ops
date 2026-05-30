"""Run named SSH command sets from project config.

The script selects an ``ssh`` set from ``config.yaml``, validates connection and
command settings, executes ``ssh.exe``, and reports common SSH failures clearly.
"""

import subprocess
from typing import Any

from winutils_python import config as config_utils
from winutils_python import config_validation
from winutils_python import config_sets, visual

CONFIG_SECTION = "ssh"


DEFAULT_SECTION = r'''ssh:
  example_set:
    user: 'user'
    host: '192.168.1.1'
    port: 22
    timeout: 300
    command: 'ls'
'''

def ensure_section(config: dict[str, Any]) -> None:
    """Ensure the SSH section exists and has table shape."""

    if CONFIG_SECTION not in config:
        config_utils.append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning(
            f"Added default '{CONFIG_SECTION}' section to config.yaml. Please configure it before running."
        )
        raise SystemExit(1)

    config_sets.section_sets(config, CONFIG_SECTION)


def report_ssh_error(title: str, message: str) -> None:
    """Print a formatted SSH error message."""

    visual.print_error(f"{title}: {message}")


def validate_ssh_config(ssh_cfg: dict[str, Any], set_name: str) -> None:
    """Report missing required configuration for one SSH set."""

    config_validation.require_set_keys(
        ssh_cfg,
        CONFIG_SECTION,
        set_name,
        (
            config_validation.required_key("user"),
            config_validation.required_key("host"),
            config_validation.required_key("port"),
            config_validation.required_key("command"),
        ),
    )


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

    cfg = config_utils.load(__file__)
    ensure_section(cfg)
    set_name = config_sets.selected_set_name(
        cfg,
        CONFIG_SECTION,
        label="SSH set",
        header="Available SSH sets:",
        empty_message=f"No SSH sets configured in config.yaml. Please add an '{CONFIG_SECTION}' section.",
        prompt="Select SSH set by number or name (or 'exit' to cancel): ",
        allow_cancel=True,
    )
    ssh_cfg = config_sets.get_set_config(
        cfg,
        CONFIG_SECTION,
        set_name,
        label="SSH set",
        allow_cancel=True,
        error_cls=ValueError,
        not_table_message=f"SSH set '{set_name}' was not found in config.yaml",
    )
    validate_ssh_config(ssh_cfg, set_name)

    user = config_utils.required_str(ssh_cfg, "user", label="SSH config value 'user'")
    host = config_utils.required_str(ssh_cfg, "host", label="SSH config value 'host'")
    port = config_utils.required_int_in_range(
        ssh_cfg,
        "port",
        label="SSH config value 'port'",
        minimum=1,
        maximum=65535,
    )
    timeout = config_utils.optional_positive_float(ssh_cfg, "timeout", label="SSH config value 'timeout'")
    command = config_utils.required_str(ssh_cfg, "command", label="SSH config value 'command'")

    visual.print_start(f"Starting SSH task: {set_name}")
    run_ssh_task(set_name, user, host, port, command, timeout)
    visual.print_done(f"SSH task finished: {set_name}")


if __name__ == "__main__":
    main()
