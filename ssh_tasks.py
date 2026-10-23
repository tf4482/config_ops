"""Run named SSH command sets from project config.

The script selects an ``ssh`` set from ``config.yaml``, validates connection and
command settings, executes ``ssh.exe``, and reports common SSH failures clearly.
"""

import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, TextIO

import yaml

# Shared helpers inlined from winutils_python

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
EMOJIS = {
    "archive": "🖼️",
    "connect": "🔌",
    "done": "🎉",
    "error": "❌",
    "list": "📋",
    "move": "📦",
    "package": "📦",
    "select": "👉",
    "start": "🚀",
    "success": "✅",
    "warning": "⚠️",
}
COLORS = {
    "error": RED,
    "info": CYAN,
    "start": BOLD + CYAN,
    "success": GREEN,
    "warning": YELLOW,
    "done": BOLD + GREEN,
}


def is_terminal(stream: TextIO | None = None) -> bool:
    """Return whether an output stream is an interactive terminal."""

    output = sys.stdout if stream is None else stream
    return bool(output and output.isatty())


def stream_safe_text(message: str, stream: TextIO) -> str:
    """Return text representable by the output stream encoding."""

    encoding = stream.encoding or "utf-8"
    return message.encode(encoding, errors="replace").decode(encoding)


def write_text(message: str, stream: TextIO, *, end: str = "\n") -> None:
    """Write UTF-8 to redirected streams and native text to terminals."""

    buffer = getattr(stream, "buffer", None)
    if not is_terminal(stream) and buffer is not None:
        buffer.write(f"{message}{end}".encode("utf-8"))
        buffer.flush()
        return

    print(stream_safe_text(message, stream), file=stream, end=end, flush=True)


def emoji_for(name: str) -> str:
    """Return a configured emoji by symbolic name, or the name itself."""

    return EMOJIS.get(name, name)


def color_for(name: str) -> str:
    """Return an ANSI color by symbolic name, or the name itself."""

    return COLORS.get(name, name)


def colorize(message: str, color: str = "", *, enabled: bool | None = None) -> str:
    """Wrap a message in ANSI color codes when color output is enabled."""

    if enabled is None:
        enabled = is_terminal()

    if not enabled or not color:
        return message

    return f"{color}{message}{RESET}"


def visual_message(
    message: str,
    *,
    emoji: str = "",
    color: str = "",
    color_enabled: bool = True,
) -> str:
    """Build a colorized message with an optional emoji prefix."""

    text = prefix_emoji(message, emoji)
    return colorize(text, color_for(color), enabled=color_enabled)


def prefix_emoji(message: str, emoji: str = "") -> str:
    """Prefix a message with an emoji when one is configured."""

    icon = emoji_for(emoji)

    if not icon:
        return message

    return f"{icon} {message}"


def print_visual(
    message: str,
    *,
    emoji: str = "",
    color: str = "",
    stream: TextIO | None = None,
) -> None:
    """Print a visual status message with terminal-aware color."""

    output = sys.stdout if stream is None else stream
    if output is None:
        return

    text = visual_message(message, emoji=emoji, color=color, color_enabled=is_terminal(output))
    write_text(text, output)


def print_start(message: str) -> None:
    """Print a start status message."""

    print_visual(message, emoji="start", color="start")


def print_list_header(message: str) -> None:
    """Print a formatted list header."""

    print_visual(message, emoji="list", color="info")


def print_list_item(index: int, message: str) -> None:
    """Print one numbered list item."""

    print_visual(f"{index}. {message}", emoji="select", color="info")


def print_warning(message: str) -> None:
    """Print a warning status message."""

    print_visual(message, emoji="warning", color="warning", stream=sys.stderr)


def print_error(message: str) -> None:
    """Print an error status message."""

    print_visual(message, emoji="error", color="error", stream=sys.stderr)


def print_done(message: str) -> None:
    """Print a completion status message."""

    print_visual(message, emoji="done", color="done")


CANCEL_CHOICES = {"exit", "quit", "cancel"}
DEFAULT_PROMPT = "Select set by number or name (or 'exit' to cancel): "


def normalize_selection_name(selection: str) -> str:
    """Normalize a CLI/menu selection by stripping option prefixes."""

    return selection.strip().lstrip("/-")


def is_cancel_choice(choice: str) -> bool:
    """Return whether a selection should cancel the menu."""

    return choice.lower() in CANCEL_CHOICES


def choose_mapping_key_terminal(
    options: Mapping[str, Any],
    *,
    header: str,
    empty_message: str,
    prompt: str = DEFAULT_PROMPT,
) -> str:
    """Prompt until the user chooses a key by name or number."""

    if not options:
        raise SystemExit(empty_message)

    names = list(options.keys())
    if sys.stdin is None or not sys.stdin.isatty():
        available = ", ".join(names)
        raise SystemExit(
            "Non-interactive input requires a set name as the first argument. "
            f"Available sets: {available}"
        )

    print_options(header, names)

    while True:
        selected_name = read_selection(prompt, options, names)

        if selected_name is not None:
            return selected_name

        print_warning("Invalid selection. Try again.")


def print_options(header: str, names: list[str]) -> None:
    """Print a numbered option list."""

    print_list_header(header)
    for index, name in enumerate(names, start=1):
        print_list_item(index, name)


def read_selection(prompt: str, options: Mapping[str, Any], names: list[str]) -> str | None:
    """Read one selection and return a matching option key when valid."""

    choice = input(prompt).strip()

    if not choice or is_cancel_choice(choice):
        raise SystemExit(0)

    normalized = normalize_selection_name(choice)
    if normalized in options:
        return normalized

    if choice.isdigit():
        return name_from_number(int(choice), names)

    return None


def name_from_number(number: int, names: list[str]) -> str | None:
    """Return the option name for a 1-based menu number."""

    if 1 <= number <= len(names):
        return names[number - 1]

    return None


CONFIG_PATH_KEY = "__config_path__"


def parse_yaml(config_text: str) -> dict[str, Any]:
    """Parse YAML text into a dictionary, treating empty content as empty."""

    loaded = yaml.safe_load(config_text)
    if loaded is None:
        return {}

    if not isinstance(loaded, dict):
        raise TypeError("Configuration root must be a table")

    return loaded


def get_table(config: dict[str, Any], name: str) -> dict[str, Any]:
    """Return a top-level table from config or raise for invalid shape."""

    value = config.get(name, {})

    if not isinstance(value, dict):
        raise TypeError(f"Configuration value '{name}' must be a table")

    return value


def required_str(config: dict[str, Any], key: str, *, label: str) -> str:
    """Return a required non-empty string config value."""

    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")

    return value.strip()


def required_int_in_range(
    config: dict[str, Any],
    key: str,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    """Return an integer config value within an inclusive range."""

    value = config.get(key)

    if isinstance(value, int) and not isinstance(value, bool):
        number = value
    elif isinstance(value, str) and value.isdigit():
        number = int(value)
    else:
        raise ValueError(f"{label} must be an integer")

    if not minimum <= number <= maximum:
        raise ValueError(f"{label} must be in range {minimum}..{maximum}")

    return number


def optional_positive_float(config: dict[str, Any], key: str, *, label: str) -> float | None:
    """Return an optional positive numeric config value."""

    value = config.get(key)

    if value is None:
        return None

    if isinstance(value, int | float) and not isinstance(value, bool):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError as error:
            raise ValueError(f"{label} must be a positive number") from error
    else:
        raise TypeError(f"{label} must be a positive number")

    if not isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be greater than 0")

    return number


def app_dir(script_file: str | Path) -> Path:
    """Return the script directory, or executable directory when frozen."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(script_file).resolve().parent


def script_dir(script_file: str | Path) -> Path:
    """Return the directory used for script-local runtime files."""

    return app_dir(script_file)


def config_path(script_file: str | Path, filename: str = "config.yaml") -> Path:
    """Return the config file path next to a script or frozen executable."""

    return script_dir(script_file) / filename


def find_config_path(script_file: str | Path, filename: str = "config.yaml") -> Path:
    """Return the config path, creating an empty file when missing."""

    path = config_path(script_file, filename)

    if not path.exists():
        path.write_text("", encoding="utf-8")

    return path


def load(script_file: str | Path, filename: str = "config.yaml") -> dict[str, Any]:
    """Load a YAML config and attach its path under an internal key."""

    path = find_config_path(script_file, filename)
    loaded_config = parse_yaml(path.read_text(encoding="utf-8"))
    loaded_config[CONFIG_PATH_KEY] = path
    return loaded_config


def append_section_yaml(config: dict[str, Any], section_yaml: str) -> None:
    """Append a default YAML section to the loaded config file."""

    path = config.get(CONFIG_PATH_KEY)

    if not isinstance(path, Path):
        return

    existing = path.read_text(encoding="utf-8").rstrip()
    separator = "\n\n" if existing else ""
    path.write_text(existing + separator + section_yaml.strip() + "\n", encoding="utf-8")


@dataclass(frozen=True)
class RequiredKey:
    """Description of a required config key."""

    key: str
    label: str | None = None
    allow_empty: bool = False


def required_key(key: str, *, label: str | None = None, allow_empty: bool = False) -> RequiredKey:
    """Return a required-key descriptor."""

    return RequiredKey(key, label=label, allow_empty=allow_empty)


def missing_keys(config: dict[str, Any], required_keys: Iterable[RequiredKey]) -> list[str]:
    """Return labels for required keys that are missing or empty."""

    missing: list[str] = []

    for required in required_keys:
        label = required.label or required.key
        if required.key not in config:
            missing.append(label)
            continue

        value = config[required.key]
        if required.allow_empty:
            continue

        if value is None or value == "" or value == [] or value == {}:
            missing.append(label)

    return missing


def report_missing_keys(missing: list[str]) -> None:
    """Print missing config keys to the console."""

    if not missing:
        return

    print_error("Missing required configuration option(s):")
    for key in missing:
        print_error(f"- {key}")


def require_keys(config: dict[str, Any], required_keys: Iterable[RequiredKey]) -> None:
    """Print and fail when required config keys are missing."""

    missing = missing_keys(config, required_keys)
    report_missing_keys(missing)

    if missing:
        raise SystemExit(1)


def require_set_keys(
    config: dict[str, Any],
    section: str,
    set_name: str,
    required_keys: Iterable[RequiredKey],
) -> None:
    """Validate required keys for a named config set."""

    prefixed_keys = (
        required_key(
            required.key,
            label=required.label or f"{section}.{set_name}.{required.key}",
            allow_empty=required.allow_empty,
        )
        for required in required_keys
    )
    require_keys(config, prefixed_keys)


def section_sets(config: dict[str, Any], section: str) -> dict[str, Any]:
    """Return configured sets from a top-level config section."""

    return get_table(config, section)


def validate_set_name(
    config: dict[str, Any],
    section: str,
    set_name: str,
    *,
    label: str,
    allow_cancel: bool = False,
) -> str:
    """Validate a requested configured set name and return it unchanged."""

    if allow_cancel and is_cancel_choice(set_name):
        raise SystemExit(0)

    sets = section_sets(config, section)

    if set_name not in sets:
        available_sets = ", ".join(sets) or "none"
        raise SystemExit(f"Unknown {label} '{set_name}'. Available sets: {available_sets}")

    return set_name


def get_set_config(
    config: dict[str, Any],
    section: str,
    set_name: str,
    *,
    label: str,
    allow_cancel: bool = False,
    error_cls: type[Exception] = TypeError,
    not_table_message: str | None = None,
) -> dict[str, Any]:
    """Return one configured set table from a top-level config section."""

    if allow_cancel and is_cancel_choice(set_name):
        raise SystemExit(0)

    set_config = section_sets(config, section).get(set_name)

    if not isinstance(set_config, dict):
        message = not_table_message or f"{label} '{set_name}' must be a table"
        raise error_cls(message)

    return set_config


def choose_set_terminal(
    config: dict[str, Any],
    section: str,
    *,
    header: str,
    empty_message: str,
    prompt: str = DEFAULT_PROMPT,
) -> str:
    """Prompt the user to choose one configured set from the terminal."""

    return choose_mapping_key_terminal(
        section_sets(config, section),
        header=header,
        empty_message=empty_message,
        prompt=prompt,
    )


def selected_set_name(
    config: dict[str, Any],
    section: str,
    *,
    label: str,
    header: str,
    empty_message: str,
    prompt: str = DEFAULT_PROMPT,
    argv: Sequence[str] | None = None,
    allow_cancel: bool = False,
) -> str:
    """Return a configured set selected from CLI args or terminal menu."""

    arguments = sys.argv if argv is None else argv

    if len(arguments) > 1:
        return validate_set_name(
            config,
            section,
            normalize_selection_name(arguments[1]),
            label=label,
            allow_cancel=allow_cancel,
        )

    return choose_set_terminal(
        config,
        section,
        header=header,
        empty_message=empty_message,
        prompt=prompt,
    )



CONFIG_SECTION = "ssh"


DEFAULT_SECTION = r'''ssh:
  example_set:
    user: 'user'
    host: '192.0.2.1'
    port: 22
    timeout: 300
    command: 'ls'
'''

def ensure_section(config: dict[str, Any]) -> None:
    """Ensure the SSH section exists and has table shape."""

    if CONFIG_SECTION not in config:
        append_section_yaml(config, DEFAULT_SECTION)
        print_warning(
            f"Added default '{CONFIG_SECTION}' section to config.yaml. Please configure it before running."
        )
        raise SystemExit(1)

    section_sets(config, CONFIG_SECTION)


def report_ssh_error(title: str, message: str) -> None:
    """Print a formatted SSH error message."""

    print_error(f"{title}: {message}")


def validate_ssh_config(ssh_cfg: dict[str, Any], set_name: str) -> None:
    """Report missing required configuration for one SSH set."""

    require_set_keys(
        ssh_cfg,
        CONFIG_SECTION,
        set_name,
        (
            required_key("user"),
            required_key("host"),
            required_key("port"),
            required_key("command"),
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
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-p",
                str(port),
                f"{user}@{host}",
                command,
            ],
            check=True,
            timeout=timeout,
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
        message = f"SSH exited with code {error.returncode}. See SSH output above."
        report_ssh_error(f"SSH task failed: {set_name}", message)
        raise


def main() -> None:
    """Run the selected SSH task set."""

    cfg = load(__file__)
    ensure_section(cfg)
    set_name = selected_set_name(
        cfg,
        CONFIG_SECTION,
        label="SSH set",
        header="Available SSH sets:",
        empty_message=f"No SSH sets configured in config.yaml. Please add an '{CONFIG_SECTION}' section.",
        prompt="Select SSH set by number or name (or 'exit' to cancel): ",
        allow_cancel=True,
    )
    ssh_cfg = get_set_config(
        cfg,
        CONFIG_SECTION,
        set_name,
        label="SSH set",
        allow_cancel=True,
        error_cls=ValueError,
        not_table_message=f"SSH set '{set_name}' was not found in config.yaml",
    )
    validate_ssh_config(ssh_cfg, set_name)

    user = required_str(ssh_cfg, "user", label="SSH config value 'user'")
    host = required_str(ssh_cfg, "host", label="SSH config value 'host'")
    port = required_int_in_range(
        ssh_cfg,
        "port",
        label="SSH config value 'port'",
        minimum=1,
        maximum=65535,
    )
    timeout = optional_positive_float(ssh_cfg, "timeout", label="SSH config value 'timeout'")
    command = required_str(ssh_cfg, "command", label="SSH config value 'command'")

    print_start(f"Starting SSH task: {set_name}")
    run_ssh_task(set_name, user, host, port, command, timeout)
    print_done(f"SSH task finished: {set_name}")


if __name__ == "__main__":
    main()
