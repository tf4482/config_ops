"""Adjust Windows file timestamps from filenames, folders, or media metadata.

The script reads named configuration sets from ``config.yaml``, optionally connects
SMB mappings, copies files when requested, and applies parsed timestamps as
creation, access, and modification times through the Win32 ``SetFileTime`` API.
It can also write media metadata from a file's existing Windows modification time.
"""

import base64
import getpass
import hashlib
import re
import shutil
import struct
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from ctypes import Structure, WinDLL, byref, c_bool, c_uint32, c_void_p, c_wchar_p
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TextIO

import yaml

# Shared helpers inlined from winutils_python

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
DEFAULT_SUCCESS_EMOJI = "success"
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


def print_info(message: str, *, emoji: str = "") -> None:
    """Print an informational status message."""

    print_visual(message, emoji=emoji, color="info")


def print_list_header(message: str) -> None:
    """Print a formatted list header."""

    print_visual(message, emoji="list", color="info")


def print_list_item(index: int, message: str) -> None:
    """Print one numbered list item."""

    print_visual(f"{index}. {message}", emoji="select", color="info")


def print_success(message: str, *, emoji: str = DEFAULT_SUCCESS_EMOJI) -> None:
    """Print a success status message."""

    print_visual(message, emoji=emoji, color="success")


def print_warning(message: str) -> None:
    """Print a warning status message."""

    print_visual(message, emoji="warning", color="warning", stream=sys.stderr)


def print_error(message: str) -> None:
    """Print an error status message."""

    print_visual(message, emoji="error", color="error", stream=sys.stderr)


def print_done(message: str) -> None:
    """Print a completion status message."""

    print_visual(message, emoji="done", color="done")


menu_CANCEL_CHOICES = {"exit", "quit", "cancel"}
DEFAULT_PROMPT = "Select set by number or name (or 'exit' to cancel): "


def normalize_selection_name(selection: str) -> str:
    """Normalize a CLI/menu selection by stripping option prefixes."""

    return selection.strip().lstrip("/-")


def is_cancel_choice(choice: str) -> bool:
    """Return whether a selection should cancel the menu."""

    return choice.lower() in menu_CANCEL_CHOICES


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


def optional_bool(config: dict[str, Any], key: str, *, label: str, default: bool) -> bool:
    """Return an optional strict boolean config value."""

    value = config.get(key, default)
    if not isinstance(value, bool):
        raise TypeError(f"Configuration value '{label}' must be true or false")

    return value


def required_list(
    config: dict[str, Any],
    key: str,
    *,
    label: str,
    non_empty: bool = False,
    empty_message: str | None = None,
) -> list[Any]:
    """Return a list config value, optionally requiring at least one item."""

    value = config.get(key, [])

    if not isinstance(value, list):
        raise TypeError(f"Configuration value '{label}' must be a list")

    if non_empty and not value:
        message = empty_message or f"Configuration value '{label}' must define at least one entry"
        raise ValueError(message)

    return value


def normalized_string_set(
    config: dict[str, Any],
    key: str,
    *,
    label: str,
    non_empty: bool = False,
    empty_list_message: str | None = None,
    empty_normalized_message: str | None = None,
) -> set[str]:
    """Return a normalized lowercase string set from a list config value."""

    values = required_list(
        config,
        key,
        label=label,
        non_empty=non_empty,
        empty_message=empty_list_message,
    )
    normalized_values = {str(value).strip().lower() for value in values if str(value).strip()}

    if not normalized_values:
        message = empty_normalized_message or f"Configuration value '{label}' must define at least one entry"
        raise ValueError(message)

    return normalized_values


def normalized_extension_set(
    config: dict[str, Any],
    key: str,
    *,
    label: str,
    non_empty: bool = False,
    empty_list_message: str | None = None,
) -> set[str]:
    """Return normalized lowercase file extensions from a list config value."""

    return normalized_string_set(
        config,
        key,
        label=label,
        non_empty=non_empty,
        empty_list_message=empty_list_message,
        empty_normalized_message=f"Configuration value '{label}' must define at least one extension",
    )


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


def config_sets_config_key(section: str, set_name: str, name: str) -> str:
    """Return a dotted config key for human-readable error messages."""

    return f"{section}.{set_name}.{name}"


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


SmbMapping = tuple[str | None, str]
DEFAULT_MAPPINGS: tuple[SmbMapping, ...] = ()
DEFAULT_USER = ""
SMB_CONFIG_SECTION = "smb"
SMB_MAPPINGS_KEY = "mappings"
SMB_USER_KEY = "user"
SMB_ENCRYPTED_PASSWORD_KEY = "encrypted_password"
SMB_PASSWORD_FILE_KEY = "password_file"
SMB_PASSWORD_KEY = "password"
connect_smb_CONFIG_PATH_KEY = "__config_path__"
NET_USE_SUCCESS_EXIT_CODE = 0
NET_USE_ALREADY_ASSIGNED_EXIT_CODE = 2
NET_USE_SUCCESS_EXIT_CODES = (NET_USE_SUCCESS_EXIT_CODE,)
PASSWORD_KEY = (
    b"winutils_python_smb_password_key_v2__"
    b"2f4b64e8f52d49efb9ab6fdb79ce38a6__"
    b"0e1d7b2e4a1e4c6eb558ca69a822f43c__"
    b"9af7c446b6d44db3a8997ef1256ca7a0__"
    b"d538a23b6a3248dfb6bd71bb35c3904f__"
    b"compiled_scripts_can_embed_this_long_static_secret__"
    b"post_quantum_symmetric_security_relies_on_long_keys__"
    b"6e5f7d2b1a304b7f941c9b8b2c6d5e3a__"
    b"b7a5d6e4c2f14098a9b8c7d6e5f4a3b2__"
    b"f1e2d3c4b5a697887766554433221100__"
    b"00112233445566778899aabbccddeeff__"
    b"ffeeddccbbaa99887766554433221100__"
    b"5c4d3e2f1a0b9c8d7e6f504132231405"
)
PASSWORD_PREFIX = "wp1:"
CREATE_NO_WINDOW = 0x08000000


def subprocess_creationflags() -> int:
    """Return subprocess flags that hide child console windows on Windows."""

    if sys.platform == "win32":
        return CREATE_NO_WINDOW

    return 0


def is_successful_net_use_return_code(return_code: int) -> bool:
    """Return whether a ``net use`` return code is considered successful."""

    return return_code in NET_USE_SUCCESS_EXIT_CODES


@dataclass(frozen=True)
class SmbMappingResult:
    """Result for one attempted SMB connection or drive mapping."""

    drive: str | None
    share: str
    return_code: int

    @property
    def failed(self) -> bool:
        """Return whether the SMB mapping failed."""

        return not is_successful_net_use_return_code(self.return_code)


class SmbConnectionError(RuntimeError):
    """Raised after one or more SMB mappings fail."""

    def __init__(self, results: list[SmbMappingResult]) -> None:
        """Build a summary error from mapping results."""

        self.results = results
        failed_results = [result for result in results if result.failed]
        summary = ", ".join(
            f"{mapping_label(result.drive, result.share)}: exit code {result.return_code}"
            for result in failed_results
        )
        super().__init__(f"{len(failed_results)} SMB connection(s) failed: {summary}")


def mapping_label(drive: str | None, share: str) -> str:
    """Return a display label for a mapped or deviceless connection."""

    return f"{drive} → {share}" if drive else share


def password_key_stream(length: int) -> bytes:
    """Return a deterministic key stream for lightweight password obfuscation."""

    stream = b""
    counter = 0

    while len(stream) < length:
        stream += hashlib.sha256(PASSWORD_KEY + counter.to_bytes(4, byteorder="little")).digest()
        counter += 1

    return stream[:length]


def encrypt_password(password: str) -> str:
    """Obfuscate an SMB password for storage in config files."""

    password_bytes = password.encode("utf-8")
    key_stream = password_key_stream(len(password_bytes))
    encrypted = xor_bytes(password_bytes, key_stream)
    return PASSWORD_PREFIX + base64.urlsafe_b64encode(encrypted).decode("ascii")


def decrypt_password(encrypted_password: str) -> str:
    """Decode an SMB password stored by :func:`encrypt_password`."""

    if not encrypted_password.startswith(PASSWORD_PREFIX):
        raise ValueError("Unsupported encrypted SMB password format")

    encrypted = base64.urlsafe_b64decode(encrypted_password.removeprefix(PASSWORD_PREFIX).encode("ascii"))
    key_stream = password_key_stream(len(encrypted))
    decrypted = xor_bytes(encrypted, key_stream)
    return decrypted.decode("utf-8")


def xor_bytes(data: bytes, key_stream: bytes) -> bytes:
    """XOR two byte strings of equal length."""

    return bytes(data_byte ^ key_byte for data_byte, key_byte in zip(data, key_stream, strict=True))


def connect_smb_parse_yaml(config_text: str) -> dict:
    """Parse YAML text into a dictionary, treating empty content as empty."""

    return yaml.safe_load(config_text) or {}


def dump_yaml(config: dict) -> str:
    """Serialize config while omitting internal helper keys."""

    clean = {key: value for key, value in config.items() if not str(key).startswith("__")}
    return yaml.safe_dump(clean, sort_keys=False, allow_unicode=True)


def replace_or_add_string_value(config_path: Path, table: str, key: str, value: str) -> None:
    """Replace or add a string value inside a YAML config table."""

    loaded_config = connect_smb_parse_yaml(config_path.read_text(encoding="utf-8"))
    table_config = loaded_config.setdefault(table, {})

    if not isinstance(table_config, dict):
        raise TypeError(f"Configuration value '{table}' must be a table")

    table_config[key] = value
    config_path.write_text(dump_yaml(loaded_config), encoding="utf-8")


def remove_value(config_path: Path, table: str, key: str) -> None:
    """Remove a key from a YAML config table when present."""

    loaded_config = connect_smb_parse_yaml(config_path.read_text(encoding="utf-8"))
    table_config = loaded_config.get(table, {})

    if isinstance(table_config, dict) and key in table_config:
        del table_config[key]
        config_path.write_text(dump_yaml(loaded_config), encoding="utf-8")


def store_prompted_password(config: dict, password: str, *, table: str = SMB_CONFIG_SECTION) -> None:
    """Persist a prompted SMB password and remove plaintext password keys."""

    config_path = config.get(connect_smb_CONFIG_PATH_KEY)

    if not isinstance(config_path, Path):
        raise TypeError(f"Loaded configuration is missing internal '{connect_smb_CONFIG_PATH_KEY}'")

    replace_or_add_string_value(config_path, table, SMB_ENCRYPTED_PASSWORD_KEY, encrypt_password(password))
    remove_value(config_path, table, SMB_PASSWORD_FILE_KEY)
    remove_value(config_path, table, SMB_PASSWORD_KEY)


def optional_smb_enabled(config: dict, *, error_label: str) -> bool:
    """Return whether a set-level optional SMB flag is enabled."""

    enabled = config.get(SMB_CONFIG_SECTION, False)

    if not isinstance(enabled, bool):
        raise TypeError(f"{error_label} must be true or false")

    return enabled


def scoped_config_for_optional_smb(config: dict, set_config: dict, *, error_label: str) -> dict:
    """Return full config when set-level SMB is enabled, otherwise without SMB."""

    scoped_config = dict(config)

    if optional_smb_enabled(set_config, error_label=error_label):
        return scoped_config

    scoped_config.pop(SMB_CONFIG_SECTION, None)
    return scoped_config


def prompt_password() -> str:
    """Prompt for an SMB password in the terminal without echoing input."""

    if sys.stdin is None or not sys.stdin.isatty():
        raise RuntimeError(
            "SMB password is unavailable in non-interactive mode. "
            "Run once interactively to store it before unattended execution."
        )

    return getpass.getpass("SMB password: ")


def resolve_password_from_config(smb_config: dict) -> tuple[str, bool]:
    """Return an SMB password and whether it was prompted interactively."""

    encrypted_password = smb_config.get(SMB_ENCRYPTED_PASSWORD_KEY)

    if isinstance(encrypted_password, str):
        try:
            return decrypt_password(encrypted_password), False
        except (ValueError, UnicodeError) as error:
            print_warning(f"Stored SMB password could not be used: {error}")

    return prompt_password(), True


def mappings_from_config(smb_config: dict) -> tuple[SmbMapping, ...]:
    """Return configured SMB connections with optional drive letters."""

    mappings = smb_config.get(SMB_MAPPINGS_KEY)

    if not isinstance(mappings, list):
        raise TypeError(f"SMB configuration must define a '{SMB_MAPPINGS_KEY}' list")

    normalized_mappings: list[SmbMapping] = []
    for index, mapping in enumerate(mappings, start=1):
        if not isinstance(mapping, dict):
            raise TypeError(f"SMB mapping {index} must be a table")

        configured_drive = mapping.get("drive")
        drive = str(configured_drive).strip() if configured_drive is not None else None
        share = str(mapping.get("share", "")).strip()
        if drive == "":
            raise ValueError(f"SMB mapping {index} value 'drive' must be non-empty when configured")
        if not share:
            raise ValueError(f"SMB mapping {index} must define a non-empty 'share' value")

        normalized_mappings.append((drive, share))

    return tuple(normalized_mappings)


def user_from_config(smb_config: dict) -> str:
    """Return the configured SMB user or raise when it is missing."""

    user = str(smb_config.get(SMB_USER_KEY, DEFAULT_USER)).strip()

    if not user:
        raise ValueError(
            "SMB configuration must define a non-empty 'user'. "
            f"Set '{SMB_CONFIG_SECTION}.{SMB_USER_KEY}', for example "
            "'DOMAIN\\user', 'SERVER\\user' or '.\\local_user'."
        )

    return user


def has_smb_config(config: dict) -> bool:
    """Return whether config contains usable top-level SMB mappings."""

    smb_config = config.get(SMB_CONFIG_SECTION)

    if smb_config is None:
        return False

    if not isinstance(smb_config, dict):
        raise TypeError(f"Configuration value '{SMB_CONFIG_SECTION}' must be a table")

    mappings = smb_config.get(SMB_MAPPINGS_KEY)
    if mappings is None:
        return False
    if not isinstance(mappings, list):
        raise TypeError(f"SMB configuration value '{SMB_MAPPINGS_KEY}' must be a list")

    return bool(mappings)


def normalize_unc_path(path: str) -> str:
    """Normalize a UNC path for case-insensitive comparison."""

    return path.rstrip("\\/").casefold()


def existing_drive_mapping(drive: str) -> str | None:
    """Return the UNC share currently mapped to a drive, if available."""

    result = subprocess.run(
        ["net", "use", drive],
        check=False,
        capture_output=True,
        text=True,
        encoding="oem",
        errors="replace",
        creationflags=subprocess_creationflags(),
    )

    if result.returncode != NET_USE_SUCCESS_EXIT_CODE:
        return None

    output = f"{result.stdout}\n{result.stderr}"
    for line in output.splitlines():
        parts = line.split()
        for part in parts:
            if part.startswith("\\\\"):
                return part

    return None


def unc_share_accessible(share: str) -> bool:
    """Return whether an existing Windows session can access the UNC share."""

    try:
        return Path(share).is_dir()
    except OSError:
        return False


def run_net_use(
    drive: str | None,
    share: str,
    password: str,
    *,
    user: str = DEFAULT_USER,
    quiet: bool = True,
) -> int:
    """Open one mapped or deviceless SMB connection and return its exit code."""

    if drive is None and unc_share_accessible(share):
        return NET_USE_SUCCESS_EXIT_CODE

    stdout = subprocess.DEVNULL if quiet else None
    stderr = subprocess.DEVNULL if quiet else None

    result = subprocess.run(
        build_net_use_command(drive, share, password, user=user),
        check=False,
        stdout=stdout,
        stderr=stderr,
        creationflags=subprocess_creationflags(),
    )

    if result.returncode != NET_USE_ALREADY_ASSIGNED_EXIT_CODE or drive is None:
        return result.returncode

    existing_share = existing_drive_mapping(drive)
    if existing_share is None:
        return NET_USE_ALREADY_ASSIGNED_EXIT_CODE

    if normalize_unc_path(existing_share) == normalize_unc_path(share):
        return NET_USE_SUCCESS_EXIT_CODE

    return result.returncode


def build_net_use_command(drive: str | None, share: str, password: str, *, user: str) -> list[str]:
    """Build a mapped or deviceless ``net use`` command."""

    connection = [drive, share] if drive else [share]
    persistence = "yes" if drive else "no"
    return [
        "net",
        "use",
        *connection,
        password,
        f"/USER:{user}",
        "/Y",
        f"/persistent:{persistence}",
    ]


def connect_smb_shares(
    password: str,
    mappings: tuple[SmbMapping, ...] = DEFAULT_MAPPINGS,
    *,
    user: str = DEFAULT_USER,
    quiet: bool | None = None,
) -> list[SmbMappingResult]:
    """Connect SMB shares and raise when any connection fails."""

    if quiet is None:
        quiet = True

    if not mappings:
        if not quiet:
            print_info("No SMB mappings configured; skipping SMB connection", emoji="connect")
        return []

    if not quiet:
        print_info("Connecting SMB shares", emoji="start")

    results: list[SmbMappingResult] = []

    for drive, share in mappings:
        if not quiet:
            print_info(f"Connecting {mapping_label(drive, share)}", emoji="connect")

        return_code = run_net_use(drive, share, password, user=user, quiet=quiet)
        results.append(SmbMappingResult(drive, share, return_code))

        if not quiet:
            print_mapping_result(drive, return_code)

    if not quiet:
        print_done("SMB share connection finished")

    failed_results = [result for result in results if result.failed]
    if failed_results:
        raise SmbConnectionError(results)

    return results


def print_mapping_result(drive: str | None, return_code: int) -> None:
    """Print a human-readable status for one SMB connection result."""

    label = drive or "UNC"

    if return_code == NET_USE_SUCCESS_EXIT_CODE:
        print_success(f"Connection {label}: OK")
        return

    if return_code == NET_USE_ALREADY_ASSIGNED_EXIT_CODE:
        print_warning(f"Connection {label}: already in use by a different share")
        return

    print_warning(f"Connection {label}: exit code {return_code}")


def connect_smb_get_table(config: dict, name: str) -> dict:
    """Return a top-level config table or raise when shape is invalid."""

    value = config.get(name, {})

    if not isinstance(value, dict):
        raise TypeError(f"Configuration value '{name}' must be a table")

    return value


def connect_from_config(
    config: dict,
    *,
    on_password_prompted: Callable[[str], None] | None = None,
) -> list[SmbMappingResult]:
    """Connect SMB shares from a loaded top-level config dictionary."""

    if not has_smb_config(config):
        print_info("No SMB mappings configured; skipping SMB connection", emoji="connect")
        return []

    smb_config = connect_smb_get_table(config, SMB_CONFIG_SECTION)
    password, prompted = resolve_password_from_config(smb_config)
    mappings = mappings_from_config(smb_config)
    user = user_from_config(smb_config)
    results = connect_smb_shares(password, mappings, user=user, quiet=True)

    if prompted and on_password_prompted is not None:
        on_password_prompted(password)

    return results



DEFAULT_SECTION = r'''adjust_file_creation_date:
  example_set:
    smb: false
    mode: file
    source_folder: 'R:\path\to\files'
    target_folder: 'C:\path\to\target'
    extensions:
      - .jpg
      - .jpeg
      - .png
      - .gif
      - .bmp
      - .tif
      - .tiff
      - .mp4
      - .mov
    change_files_in_place: true
    overwrite: false
    hour_adjustment: 0
    patterns:
      - pattern: '^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})(?:[ _-]+(?P<hour>\d{2})_(?P<minute>\d{2})_(?P<second>\d{2}))?'
'''

INVALID_HANDLE_VALUE = c_void_p(-1).value
WINDOWS_TICK = 10_000_000
WINDOWS_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)
CONFIG_SECTION = "adjust_file_creation_date"
MODE_FILE = "file"
MODE_FOLDER = "folder"
MODE_METADATA = "metadata"
MODE_METADATA_REVERSE = "metadata_reverse"
VALID_MODES = {MODE_FILE, MODE_FOLDER, MODE_METADATA, MODE_METADATA_REVERSE}
PATTERN_MODES = {MODE_FILE, MODE_FOLDER}
DEFAULT_SMB_REGISTRY_PATH = "Software\\peripherals"
CANCEL_CHOICES = {"exit", "quit", "cancel"}
FILE_READ_ATTRIBUTES = 0x0080
FILE_WRITE_ATTRIBUTES = 0x0100
FILE_SHARE_READ_WRITE_DELETE = 0x00000001 | 0x00000002 | 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
EXIF_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff"}
QUICKTIME_VIDEO_EXTENSIONS = {".3g2", ".3gp", ".m4v", ".mov", ".mp4"}
EXIF_DATETIME_TAGS = (0x9003, 0x9004, 0x0132)
METADATA_DATETIME_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
QUICKTIME_EPOCH = datetime(1904, 1, 1, tzinfo=UTC)
QUICKTIME_CONTAINER_ATOMS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"meta", b"ilst"}
QUICKTIME_CREATION_TIME_ATOMS = {b"mvhd", b"tkhd", b"mdhd"}

kernel32 = WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.argtypes = (
    c_wchar_p,
    c_uint32,
    c_uint32,
    c_void_p,
    c_uint32,
    c_uint32,
    c_void_p,
)
kernel32.CreateFileW.restype = c_void_p
kernel32.GetFileTime.argtypes = (c_void_p, c_void_p, c_void_p, c_void_p)
kernel32.GetFileTime.restype = c_bool
kernel32.SetFileTime.argtypes = (c_void_p, c_void_p, c_void_p, c_void_p)
kernel32.SetFileTime.restype = c_bool
kernel32.CloseHandle.argtypes = (c_void_p,)
kernel32.CloseHandle.restype = c_bool


@dataclass(frozen=True)
class FileAdjustmentResult:
    """Result for a single source file processed by the adjustment workflow."""

    source: Path
    destination: Path | None = None
    timestamp: datetime | None = None
    changed: bool = False
    error: Exception | None = None

    @property
    def failed(self) -> bool:
        """Return whether this file failed during processing."""

        return self.error is not None


class FileCreationDateAdjustmentError(RuntimeError):
    """Raised after processing when one or more file adjustments failed."""

    def __init__(self, results: list[FileAdjustmentResult]) -> None:
        """Build a summary error from failed adjustment results."""

        self.results = results
        failed_results = [result for result in results if result.failed]
        summary = ", ".join(f"{result.source}: {result.error}" for result in failed_results)
        super().__init__(f"{len(failed_results)} file creation date adjustment(s) failed: {summary}")

def ensure_section(config: dict[str, Any]) -> None:
    """Ensure the adjustment section exists and has table shape."""

    if CONFIG_SECTION not in config:
        append_section_yaml(config, DEFAULT_SECTION)
        print_warning(
            f"Added default '{CONFIG_SECTION}' section to config.yaml. "
            "Please configure it before running."
        )
        raise SystemExit(1)

    section_sets(config, CONFIG_SECTION)


def config_key(set_name: str, name: str) -> str:
    """Return a human-readable dotted config key for error messages."""

    return config_sets_config_key(CONFIG_SECTION, set_name, name)


def registry_path_from_config(script_config: dict[str, Any], set_name: str) -> str:
    """Return the registry path used by related SMB/peripheral integrations."""

    registry_path = str(script_config.get("smb_registry_path", DEFAULT_SMB_REGISTRY_PATH)).strip()

    if not registry_path:
        raise ValueError(f"Configuration value '{config_key(set_name, 'smb_registry_path')}' must be set")

    return registry_path


def source_folder_from_config(script_config: dict[str, Any]) -> Path:
    """Return the source folder configured for the adjustment set."""

    return Path(str(script_config["source_folder"]))


def mode_from_config(script_config: dict[str, Any], set_name: str) -> str:
    """Return the configured timestamp source mode."""

    mode = str(script_config["mode"]).strip().lower().replace("-", "_").replace(" ", "_")

    if mode not in VALID_MODES:
        valid_modes = ", ".join(sorted(VALID_MODES))
        raise ValueError(f"Configuration value '{config_key(set_name, 'mode')}' must be one of: {valid_modes}")

    return mode


def change_files_in_place_from_config(script_config: dict[str, Any]) -> bool:
    """Return whether matching files should be modified in place."""

    return optional_bool(
        script_config,
        "change_files_in_place",
        label="change_files_in_place",
        default=True,
    )


def overwrite_from_config(script_config: dict[str, Any]) -> bool:
    """Return whether copied target files may be overwritten."""

    return optional_bool(
        script_config,
        "overwrite",
        label="overwrite",
        default=False,
    )


def target_folder_from_config(
    script_config: dict[str, Any],
    source_folder: Path,
    change_files_in_place: bool,
) -> Path:
    """Return the destination folder for copied files."""

    if change_files_in_place:
        return source_folder

    target_folder = script_config.get("target_folder")

    if target_folder:
        return Path(str(target_folder))

    return source_folder / "changed_date"


def hour_adjustment_from_config(script_config: dict[str, Any]) -> int:
    """Return the configured hour offset applied to parsed timestamps."""

    return int(script_config.get("hour_adjustment", 0))


def get_file_extensions(script_config: dict[str, Any], set_name: str) -> set[str]:
    """Return normalized lowercase file extensions to process."""

    return normalized_extension_set(
        script_config,
        "extensions",
        label=config_key(set_name, "extensions"),
        non_empty=True,
    )


def get_patterns(script_config: dict[str, Any], set_name: str) -> list[re.Pattern[str]]:
    """Compile and validate configured filename timestamp regex patterns."""

    pattern_configs = required_list(
        script_config,
        "patterns",
        label=config_key(set_name, "patterns"),
        non_empty=True,
    )

    compiled_patterns: list[re.Pattern] = []
    for index, pattern_config in enumerate(pattern_configs, start=1):
        if not isinstance(pattern_config, dict):
            raise TypeError(f"Pattern entry {index} must be a table with a 'pattern' value")

        pattern_value = pattern_config.get("pattern")
        if not isinstance(pattern_value, str) or not pattern_value:
            raise ValueError(f"Pattern entry {index} must define a non-empty 'pattern' string")

        try:
            compiled_pattern = re.compile(pattern_value, flags=re.IGNORECASE)
        except re.error as error:
            raise ValueError(f"Pattern entry {index} has invalid regex syntax: {error}") from error

        group_names = set(compiled_pattern.groupindex)
        has_year_group = "year" in group_names or "year2" in group_names
        has_month_day_groups = {"month", "day"}.issubset(group_names)

        if not has_year_group or not has_month_day_groups:
            raise ValueError(f"Pattern entry {index} must define named groups year or year2, plus month and day")

        compiled_patterns.append(compiled_pattern)

    return compiled_patterns


def get_patterns_for_mode(script_config: dict[str, Any], set_name: str, mode: str) -> list[re.Pattern[str]]:
    """Return regex patterns only for modes that parse filename/folder text."""

    if mode not in PATTERN_MODES:
        return []

    return get_patterns(script_config, set_name)


def validate_script_config(script_config: dict[str, Any], set_name: str) -> None:
    """Report missing required configuration for one adjustment set."""

    require_set_keys(
        script_config,
        CONFIG_SECTION,
        set_name,
        (
            required_key("mode"),
            required_key("source_folder"),
            required_key("extensions"),
        ),
    )

    mode = mode_from_config(script_config, set_name)
    if mode in PATTERN_MODES:
        require_set_keys(
            script_config,
            CONFIG_SECTION,
            set_name,
            (required_key("patterns"),),
        )


def datetime_to_filetime(timestamp: datetime) -> tuple[int, int]:
    """Convert a Python datetime into low/high Windows FILETIME integers."""

    utc_timestamp = timestamp.astimezone(UTC)
    ticks = int((utc_timestamp - WINDOWS_EPOCH).total_seconds() * WINDOWS_TICK)
    return ticks & 0xFFFFFFFF, ticks >> 32


def filetime_to_datetime(filetime: "FileTime") -> datetime:
    """Convert a Win32 FILETIME structure into a local Python datetime."""

    ticks = filetime.dwLowDateTime + (filetime.dwHighDateTime << 32)
    return (WINDOWS_EPOCH + timedelta(seconds=ticks / WINDOWS_TICK)).astimezone()


class FileTime(Structure):
    """ctypes representation of the Win32 FILETIME structure."""

    _fields_ = (("dwLowDateTime", c_uint32), ("dwHighDateTime", c_uint32))


def open_file_time_handle(path: Path, access: int) -> c_void_p:
    """Open a Windows file handle suitable for reading or writing timestamps."""

    handle = kernel32.CreateFileW(
        str(path),
        access,
        FILE_SHARE_READ_WRITE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )

    if handle == INVALID_HANDLE_VALUE:
        raise OSError(f"Could not open file for timestamp access: {path}")

    return handle


def get_file_times(path: Path) -> tuple[FileTime, FileTime, FileTime]:
    """Return raw creation, access, and modification FILETIME values."""

    handle = open_file_time_handle(path, FILE_READ_ATTRIBUTES)
    creation_time = FileTime()
    access_time = FileTime()
    modification_time = FileTime()

    try:
        success = kernel32.GetFileTime(handle, byref(creation_time), byref(access_time), byref(modification_time))
        if not success:
            raise OSError(f"Could not read timestamps: {path}")
    finally:
        kernel32.CloseHandle(handle)

    return (
        FileTime(creation_time.dwLowDateTime, creation_time.dwHighDateTime),
        FileTime(access_time.dwLowDateTime, access_time.dwHighDateTime),
        FileTime(modification_time.dwLowDateTime, modification_time.dwHighDateTime),
    )


def set_raw_file_times(path: Path, creation_time: FileTime, access_time: FileTime, modification_time: FileTime) -> None:
    """Set raw creation, access, and modification FILETIME values."""

    handle = open_file_time_handle(path, FILE_WRITE_ATTRIBUTES)

    try:
        success = kernel32.SetFileTime(handle, byref(creation_time), byref(access_time), byref(modification_time))
        if not success:
            raise OSError(f"Could not set timestamps: {path}")
    finally:
        kernel32.CloseHandle(handle)


def set_creation_and_modification_times(path: Path, timestamp: datetime) -> None:
    """Set creation and modification times for a Windows file."""

    low_date_time, high_date_time = datetime_to_filetime(timestamp)
    filetime = FileTime(low_date_time, high_date_time)
    set_raw_file_times(path, filetime, filetime, filetime)


def file_modification_timestamp(path: Path, hour_adjustment: int) -> datetime:
    """Return a file's Windows modification time as a local datetime."""

    _, _, modification_time = get_file_times(path)
    timestamp = filetime_to_datetime(modification_time)

    if hour_adjustment:
        timestamp += timedelta(hours=hour_adjustment)

    return timestamp


def parse_timestamp_text(value: str, patterns: list[re.Pattern[str]], hour_adjustment: int) -> datetime | None:
    """Parse a timestamp from text using the first matching pattern."""

    for pattern in patterns:
        match = pattern.match(value)

        if match is None:
            continue

        values = match.groupdict()
        year = values.get("year")
        year2 = values.get("year2")
        month = values.get("month")
        day = values.get("day")
        hour = values.get("hour") or "00"
        minute = values.get("minute") or "00"
        second = values.get("second") or "00"

        if year is None and year2 is not None:
            year = f"20{year2}"

        if year is None or month is None or day is None:
            raise ValueError(f"Pattern must provide year/year2, month, and day groups: {pattern.pattern}")

        timestamp = datetime.strptime(
            f"{year}{month}{day}{hour}{minute}{second}",
            "%Y%m%d%H%M%S",
        ).astimezone()

        if hour_adjustment:
            timestamp += timedelta(hours=hour_adjustment)

        return timestamp

    return None


def parse_timestamp(path: Path, patterns: list[re.Pattern[str]], hour_adjustment: int) -> datetime | None:
    """Parse a timestamp from a filename using the first matching pattern."""

    return parse_timestamp_text(path.stem, patterns, hour_adjustment)


def parse_folder_timestamp(folder: Path, patterns: list[re.Pattern[str]], hour_adjustment: int) -> datetime | None:
    """Parse a timestamp from a folder name using the first matching pattern."""

    return parse_timestamp_text(folder.name, patterns, hour_adjustment)


def folder_timestamp_source(source_file: Path, source_folder: Path) -> Path:
    """Return the folder name that should provide a file's timestamp."""

    if source_file.parent == source_folder:
        return source_folder

    return source_file.parent


def metadata_timestamp(path: Path, hour_adjustment: int) -> datetime | None:
    """Return the first supported embedded image or video timestamp."""

    suffix = path.suffix.lower()

    if suffix in EXIF_IMAGE_EXTENSIONS:
        timestamp = exif_timestamp(path)
    elif suffix in QUICKTIME_VIDEO_EXTENSIONS:
        timestamp = quicktime_timestamp(path)
    else:
        timestamp = None

    if timestamp is not None and hour_adjustment:
        timestamp += timedelta(hours=hour_adjustment)

    return timestamp


def exif_timestamp(path: Path) -> datetime | None:
    """Read common EXIF date/time tags from JPEG or TIFF metadata."""

    try:
        data = path.read_bytes()
    except OSError:
        return None

    tiff_data = tiff_bytes_from_image(data)
    if tiff_data is None:
        return None

    return timestamp_from_tiff(tiff_data)


def tiff_bytes_from_image(data: bytes) -> bytes | None:
    """Return TIFF/EXIF bytes from JPEG APP1 data or a TIFF image."""

    if data.startswith((b"II*\x00", b"MM\x00*")):
        return data

    if not data.startswith(b"\xff\xd8"):
        return None

    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            break

        marker = data[offset + 1]
        offset += 2

        if marker in {0xD8, 0xD9}:
            continue

        if offset + 2 > len(data):
            break

        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        segment_start = offset + 2
        segment_end = offset + segment_length

        if segment_length < 2 or segment_end > len(data):
            break

        segment = data[segment_start:segment_end]
        if marker == 0xE1 and segment.startswith(b"Exif\x00\x00"):
            return segment[6:]

        offset = segment_end

    return None


def timestamp_from_tiff(data: bytes) -> datetime | None:
    """Read a timestamp from TIFF IFD metadata."""

    endian = tiff_endian(data)
    if endian is None or len(data) < 8:
        return None

    first_ifd_offset = int.from_bytes(data[4:8], endian)
    return timestamp_from_ifd_chain(data, endian, first_ifd_offset, visited=set())


def tiff_endian(data: bytes) -> Literal["little", "big"] | None:
    """Return the byte order for TIFF metadata."""

    if data.startswith(b"II*\x00"):
        return "little"

    if data.startswith(b"MM\x00*"):
        return "big"

    return None


def timestamp_from_ifd_chain(
    data: bytes,
    endian: Literal["little", "big"],
    offset: int,
    *,
    visited: set[int],
) -> datetime | None:
    """Search an IFD and linked/sub IFDs for EXIF date/time tags."""

    if offset in visited or offset + 2 > len(data):
        return None

    visited.add(offset)

    entry_count = int.from_bytes(data[offset : offset + 2], endian)
    entries_start = offset + 2
    entries_end = entries_start + entry_count * 12

    if entries_end > len(data):
        return None

    linked_ifd_offsets: list[int] = []

    for entry_offset in range(entries_start, entries_end, 12):
        tag, field_type, count, value_offset = tiff_ifd_entry(data, endian, entry_offset)

        if tag in EXIF_DATETIME_TAGS:
            timestamp = parse_metadata_datetime(tiff_ascii_value(data, endian, field_type, count, value_offset))
            if timestamp is not None:
                return timestamp

        if tag in {0x8769, 0x8825} and value_offset:
            linked_ifd_offsets.append(value_offset)

    next_ifd_pointer = entries_end
    if next_ifd_pointer + 4 <= len(data):
        next_ifd_offset = int.from_bytes(data[next_ifd_pointer : next_ifd_pointer + 4], endian)
        if next_ifd_offset:
            linked_ifd_offsets.append(next_ifd_offset)

    for linked_offset in linked_ifd_offsets:
        timestamp = timestamp_from_ifd_chain(data, endian, linked_offset, visited=visited)
        if timestamp is not None:
            return timestamp

    return None


def tiff_ifd_entry(
    data: bytes,
    endian: Literal["little", "big"],
    offset: int,
) -> tuple[int, int, int, int]:
    """Return tag, type, count, and integer value/offset from one TIFF IFD entry."""

    tag = int.from_bytes(data[offset : offset + 2], endian)
    field_type = int.from_bytes(data[offset + 2 : offset + 4], endian)
    count = int.from_bytes(data[offset + 4 : offset + 8], endian)
    value_offset = int.from_bytes(data[offset + 8 : offset + 12], endian)
    return tag, field_type, count, value_offset


def tiff_ascii_value(
    data: bytes,
    endian: Literal["little", "big"],
    field_type: int,
    count: int,
    value_offset: int,
) -> str | None:
    """Return an ASCII string from a TIFF IFD entry value."""

    if field_type != 2 or count <= 0:
        return None

    if count <= 4:
        raw_value = value_offset.to_bytes(4, endian)[:count]
    elif value_offset + count <= len(data):
        raw_value = data[value_offset : value_offset + count]
    else:
        return None

    return raw_value.rstrip(b"\x00").decode("ascii", errors="ignore").strip()


def parse_metadata_datetime(value: str | None) -> datetime | None:
    """Parse common image/video metadata date-time text as local time."""

    if not value:
        return None

    normalized = value.strip().removesuffix("Z").strip()

    for date_format in METADATA_DATETIME_FORMATS:
        try:
            return datetime.strptime(normalized[:19], date_format).astimezone()
        except ValueError:
            continue

    return None


def metadata_datetime_text(timestamp: datetime) -> str:
    """Return EXIF-compatible local date/time text."""

    return timestamp.astimezone().strftime("%Y:%m:%d %H:%M:%S")


def write_image_metadata_timestamp(path: Path, timestamp: datetime) -> None:
    """Write common EXIF date-taken tags using the Python exif package."""

    try:
        from exif import Image
    except ImportError as error:
        raise RuntimeError("Missing dependency 'exif'. Run 'uv sync' before using metadata_reverse mode.") from error

    timestamp_text = metadata_datetime_text(timestamp)

    with path.open("rb") as file:
        image = Image(file)

    image.datetime = timestamp_text
    image.datetime_original = timestamp_text
    image.datetime_digitized = timestamp_text

    path.write_bytes(image.get_file())


def quicktime_seconds(timestamp: datetime) -> int:
    """Return QuickTime epoch seconds for a local or aware datetime."""

    seconds = int((timestamp.astimezone(UTC) - QUICKTIME_EPOCH).total_seconds())
    if seconds < 0:
        raise ValueError(f"Timestamp predates the QuickTime epoch: {timestamp:%Y-%m-%d %H:%M:%S}")

    return seconds


def quicktime_timestamp(path: Path) -> datetime | None:
    """Read a QuickTime/MP4 creation timestamp from movie header metadata."""

    try:
        with path.open("rb") as file:
            return quicktime_timestamp_from_range(file, 0, path.stat().st_size)
    except OSError:
        return None


def quicktime_timestamp_from_range(file: Any, start: int, end: int) -> datetime | None:
    """Search a byte range of QuickTime atoms for a movie-header creation time."""

    offset = start
    while offset + 8 <= end:
        atom_size, atom_type, header_size = read_quicktime_atom_header(file, offset, end)
        if atom_size is None or atom_type is None or header_size is None:
            break

        atom_end = offset + atom_size
        payload_start = offset + header_size

        if atom_end > end or atom_size < header_size:
            break

        if atom_type == b"mvhd":
            timestamp = quicktime_mvhd_timestamp(file, payload_start, atom_end)
            if timestamp is not None:
                return timestamp
        elif atom_type in QUICKTIME_CONTAINER_ATOMS:
            child_start = payload_start + (4 if atom_type == b"meta" else 0)
            timestamp = quicktime_timestamp_from_range(file, child_start, atom_end)
            if timestamp is not None:
                return timestamp

        offset = atom_end

    return None


def read_quicktime_atom_header(file: Any, offset: int, end: int) -> tuple[int | None, bytes | None, int | None]:
    """Read a QuickTime atom header at an offset."""

    file.seek(offset)
    header = file.read(8)
    if len(header) != 8:
        return None, None, None

    atom_size, atom_type = struct.unpack(">I4s", header)

    if atom_size == 1:
        extended_size_data = file.read(8)
        if len(extended_size_data) != 8:
            return None, None, None
        atom_size = struct.unpack(">Q", extended_size_data)[0]
        header_size = 16
    elif atom_size == 0:
        atom_size = end - offset
        header_size = 8
    else:
        header_size = 8

    return atom_size, atom_type, header_size


def quicktime_mvhd_timestamp(file: Any, start: int, end: int) -> datetime | None:
    """Read the creation time from a QuickTime mvhd atom payload."""

    file.seek(start)
    version_data = file.read(1)
    if len(version_data) != 1:
        return None

    version = version_data[0]
    file.seek(start + 4)

    if version == 1:
        if start + 12 > end:
            return None
        raw_timestamp = file.read(8)
        if len(raw_timestamp) != 8:
            return None
        seconds = struct.unpack(">Q", raw_timestamp)[0]
    else:
        if start + 8 > end:
            return None
        raw_timestamp = file.read(4)
        if len(raw_timestamp) != 4:
            return None
        seconds = struct.unpack(">I", raw_timestamp)[0]

    if seconds == 0:
        return None

    return (QUICKTIME_EPOCH + timedelta(seconds=seconds)).astimezone()


def write_quicktime_metadata_timestamp(path: Path, timestamp: datetime) -> None:
    """Write QuickTime-family media creation timestamps in place."""

    seconds = quicktime_seconds(timestamp)
    with path.open("r+b") as file:
        changed = write_quicktime_timestamp_in_range(file, 0, path.stat().st_size, seconds)

    if not changed:
        raise ValueError(f"No writable QuickTime creation metadata found: {path}")


def write_quicktime_timestamp_in_range(file: Any, start: int, end: int, seconds: int) -> bool:
    """Write creation timestamps in supported QuickTime atoms inside a byte range."""

    changed = False
    offset = start

    while offset + 8 <= end:
        atom_size, atom_type, header_size = read_quicktime_atom_header(file, offset, end)
        if atom_size is None or atom_type is None or header_size is None:
            break

        atom_end = offset + atom_size
        payload_start = offset + header_size

        if atom_end > end or atom_size < header_size:
            break

        if atom_type in QUICKTIME_CREATION_TIME_ATOMS:
            changed = write_quicktime_atom_creation_time(file, payload_start, atom_end, seconds) or changed
        elif atom_type in QUICKTIME_CONTAINER_ATOMS:
            child_start = payload_start + (4 if atom_type == b"meta" else 0)
            changed = write_quicktime_timestamp_in_range(file, child_start, atom_end, seconds) or changed

        offset = atom_end

    return changed


def write_quicktime_atom_creation_time(file: Any, start: int, end: int, seconds: int) -> bool:
    """Write one QuickTime atom's creation-time field."""

    file.seek(start)
    version_data = file.read(1)
    if len(version_data) != 1:
        return False

    version = version_data[0]
    creation_time_offset = start + 4

    if version == 1:
        if creation_time_offset + 8 > end:
            return False
        file.seek(creation_time_offset)
        file.write(struct.pack(">Q", seconds))
        return True

    if creation_time_offset + 4 > end:
        return False

    if seconds > 0xFFFFFFFF:
        raise ValueError("QuickTime version 0 metadata cannot store timestamps after 2040-02-06")

    file.seek(creation_time_offset)
    file.write(struct.pack(">I", seconds))
    return True


def write_metadata_timestamp(path: Path, timestamp: datetime) -> bool:
    """Write supported image/video metadata timestamp fields."""

    suffix = path.suffix.lower()

    if suffix in EXIF_IMAGE_EXTENSIONS:
        write_image_metadata_timestamp(path, timestamp)
        return True

    if suffix in QUICKTIME_VIDEO_EXTENSIONS:
        write_quicktime_metadata_timestamp(path, timestamp)
        return True

    return False


def prepare_target_folder(change_files_in_place: bool, target_folder: Path) -> None:
    """Create the target folder when files are copied before modification."""

    if change_files_in_place:
        return

    target_folder.mkdir(parents=True, exist_ok=True)


def collision_safe_path(path: Path) -> Path:
    """Return a non-existing path by appending a numeric suffix when needed."""

    if not path.exists():
        return path

    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def prepare_destination(
    source_file: Path,
    *,
    change_files_in_place: bool,
    target_folder: Path,
    overwrite: bool,
    relative_root: Path | None = None,
) -> Path | None:
    """Return the file to update, copying it first when not modifying in place."""

    if change_files_in_place:
        return source_file

    destination = target_folder / source_file.name
    if relative_root is not None:
        destination = target_folder / source_file.relative_to(relative_root)

    if not overwrite:
        destination = collision_safe_path(destination)

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, destination)
    except OSError as error:
        print_warning(f"Skipped {source_file.name}: {error}")
        return None

    return destination


def is_relative_to(path: Path, root: Path) -> bool:
    """Return whether path is located below root."""

    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False

    return True


def matching_files(
    source_folder: Path,
    extensions: set[str],
    *,
    recursive: bool,
    excluded_folder: Path | None = None,
) -> tuple[Path, ...]:
    """Return matching source files, optionally including all subfolders."""

    paths = source_folder.rglob("*") if recursive else source_folder.iterdir()
    return tuple(
        path
        for path in paths
        if path.is_file()
        and path.suffix.lower() in extensions
        and (excluded_folder is None or not is_relative_to(path, excluded_folder))
    )


def adjust_one_file(
    source_file: Path,
    timestamp: datetime,
    *,
    change_files_in_place: bool,
    target_folder: Path,
    overwrite: bool,
    relative_root: Path | None = None,
) -> FileAdjustmentResult | None:
    """Copy when needed, update one file timestamp, and return its result."""

    destination = prepare_destination(
        source_file,
        change_files_in_place=change_files_in_place,
        target_folder=target_folder,
        overwrite=overwrite,
        relative_root=relative_root,
    )
    if destination is None:
        return None

    set_creation_and_modification_times(destination, timestamp)
    print_success(f"Updated {destination.name} → {timestamp:%Y-%m-%d %H:%M:%S}")
    return FileAdjustmentResult(source_file, destination, timestamp, changed=True)


def adjust_files_from_filenames(
    source_folder: Path,
    *,
    change_files_in_place: bool,
    target_folder: Path,
    overwrite: bool,
    extensions: set[str],
    patterns: list[re.Pattern[str]],
    hour_adjustment: int,
    excluded_folder: Path | None,
) -> list[FileAdjustmentResult]:
    """Adjust matching files using timestamps parsed from each filename."""

    results: list[FileAdjustmentResult] = []

    for source_file in matching_files(source_folder, extensions, recursive=False, excluded_folder=excluded_folder):
        try:
            timestamp = parse_timestamp(source_file, patterns, hour_adjustment)
            if timestamp is None:
                continue

            result = adjust_one_file(
                source_file,
                timestamp,
                change_files_in_place=change_files_in_place,
                target_folder=target_folder,
                overwrite=overwrite,
            )
            if result is not None:
                results.append(result)
        except Exception as error:  # noqa: BLE001
            print_error(f"File creation date adjustment failed: {source_file}: {error}")
            results.append(FileAdjustmentResult(source_file, error=error))

    return results


def adjust_files_from_folder_names(
    source_folder: Path,
    *,
    change_files_in_place: bool,
    target_folder: Path,
    overwrite: bool,
    extensions: set[str],
    patterns: list[re.Pattern[str]],
    hour_adjustment: int,
    excluded_folder: Path | None,
) -> list[FileAdjustmentResult]:
    """Adjust matching files recursively using timestamps parsed from containing folder names."""

    results: list[FileAdjustmentResult] = []
    source_files = matching_files(source_folder, extensions, recursive=True, excluded_folder=excluded_folder)

    for source_file in source_files:
        if not change_files_in_place and is_relative_to(source_file, target_folder):
            continue

        try:
            timestamp = parse_folder_timestamp(
                folder_timestamp_source(source_file, source_folder),
                patterns,
                hour_adjustment,
            )
            if timestamp is None:
                continue

            result = adjust_one_file(
                source_file,
                timestamp,
                change_files_in_place=change_files_in_place,
                target_folder=target_folder,
                overwrite=overwrite,
                relative_root=source_folder,
            )
            if result is not None:
                results.append(result)
        except Exception as error:  # noqa: BLE001
            print_error(f"Folder-based file creation date adjustment failed: {source_file}: {error}")
            results.append(FileAdjustmentResult(source_file, error=error))

    return results


def adjust_files_from_metadata(
    source_folder: Path,
    *,
    change_files_in_place: bool,
    target_folder: Path,
    overwrite: bool,
    extensions: set[str],
    hour_adjustment: int,
    excluded_folder: Path | None,
) -> list[FileAdjustmentResult]:
    """Adjust matching files recursively using embedded image or video metadata timestamps."""

    results: list[FileAdjustmentResult] = []
    source_files = matching_files(source_folder, extensions, recursive=True, excluded_folder=excluded_folder)

    for source_file in source_files:
        if not change_files_in_place and is_relative_to(source_file, target_folder):
            continue

        try:
            timestamp = metadata_timestamp(source_file, hour_adjustment)
            if timestamp is None:
                continue

            result = adjust_one_file(
                source_file,
                timestamp,
                change_files_in_place=change_files_in_place,
                target_folder=target_folder,
                overwrite=overwrite,
                relative_root=source_folder,
            )
            if result is not None:
                results.append(result)
        except Exception as error:  # noqa: BLE001
            print_error(f"Metadata-based file creation date adjustment failed: {source_file}: {error}")
            results.append(FileAdjustmentResult(source_file, error=error))

    return results


def adjust_one_metadata_reverse_file(
    source_file: Path,
    *,
    change_files_in_place: bool,
    target_folder: Path,
    overwrite: bool,
    hour_adjustment: int,
    relative_root: Path,
) -> FileAdjustmentResult | None:
    """Copy when needed, then write embedded metadata from the file modification time."""

    timestamp = file_modification_timestamp(source_file, hour_adjustment)
    destination = prepare_destination(
        source_file,
        change_files_in_place=change_files_in_place,
        target_folder=target_folder,
        overwrite=overwrite,
        relative_root=relative_root,
    )
    if destination is None:
        return None

    original_times = get_file_times(destination)
    try:
        metadata_written = write_metadata_timestamp(destination, timestamp)
    finally:
        # Metadata writes often touch modification time; restore filesystem timestamps.
        set_raw_file_times(destination, *original_times)

    if not metadata_written:
        return None

    print_success(f"Updated metadata {destination.name} → {timestamp:%Y-%m-%d %H:%M:%S}")
    return FileAdjustmentResult(source_file, destination, timestamp, changed=True)


def adjust_files_from_modification_dates_to_metadata(
    source_folder: Path,
    *,
    change_files_in_place: bool,
    target_folder: Path,
    overwrite: bool,
    extensions: set[str],
    hour_adjustment: int,
    excluded_folder: Path | None,
) -> list[FileAdjustmentResult]:
    """Write media metadata timestamps from each file's Windows modification date."""

    results: list[FileAdjustmentResult] = []
    source_files = matching_files(source_folder, extensions, recursive=True, excluded_folder=excluded_folder)

    for source_file in source_files:
        if not change_files_in_place and is_relative_to(source_file, target_folder):
            continue

        try:
            result = adjust_one_metadata_reverse_file(
                source_file,
                change_files_in_place=change_files_in_place,
                target_folder=target_folder,
                overwrite=overwrite,
                hour_adjustment=hour_adjustment,
                relative_root=source_folder,
            )
            if result is not None:
                results.append(result)
        except Exception as error:  # noqa: BLE001
            print_error(f"Reverse metadata adjustment failed: {source_file}: {error}")
            results.append(FileAdjustmentResult(source_file, error=error))

    return results


def adjust_file_creation_dates(script_config: dict, set_name: str) -> list[FileAdjustmentResult]:
    """Process all matching files for one adjustment set and collect results."""

    source_folder = source_folder_from_config(script_config)
    mode = mode_from_config(script_config, set_name)
    change_files_in_place = change_files_in_place_from_config(script_config)
    overwrite = overwrite_from_config(script_config)
    target_folder = target_folder_from_config(script_config, source_folder, change_files_in_place)
    extensions = get_file_extensions(script_config, set_name)
    patterns = get_patterns_for_mode(script_config, set_name, mode)
    hour_adjustment = hour_adjustment_from_config(script_config)
    excluded_folder = None
    if not change_files_in_place and is_relative_to(target_folder, source_folder):
        excluded_folder = target_folder

    prepare_target_folder(change_files_in_place, target_folder)

    print_info(f"Adjusting file creation dates in {source_folder} using {mode} mode", emoji="archive")

    if mode == MODE_FOLDER:
        return adjust_files_from_folder_names(
            source_folder,
            change_files_in_place=change_files_in_place,
            target_folder=target_folder,
            overwrite=overwrite,
            extensions=extensions,
            patterns=patterns,
            hour_adjustment=hour_adjustment,
            excluded_folder=excluded_folder,
        )

    if mode == MODE_METADATA:
        return adjust_files_from_metadata(
            source_folder,
            change_files_in_place=change_files_in_place,
            target_folder=target_folder,
            overwrite=overwrite,
            extensions=extensions,
            hour_adjustment=hour_adjustment,
            excluded_folder=excluded_folder,
        )

    if mode == MODE_METADATA_REVERSE:
        return adjust_files_from_modification_dates_to_metadata(
            source_folder,
            change_files_in_place=change_files_in_place,
            target_folder=target_folder,
            overwrite=overwrite,
            extensions=extensions,
            hour_adjustment=hour_adjustment,
            excluded_folder=excluded_folder,
        )

    return adjust_files_from_filenames(
        source_folder,
        change_files_in_place=change_files_in_place,
        target_folder=target_folder,
        overwrite=overwrite,
        extensions=extensions,
        patterns=patterns,
        hour_adjustment=hour_adjustment,
        excluded_folder=excluded_folder,
    )


def summarize_adjustment_results(results: list[FileAdjustmentResult]) -> None:
    """Print a compact summary of successful and failed file adjustments."""

    failed_results = [result for result in results if result.failed]
    changed_count = sum(1 for result in results if result.changed)

    print_info(
        "File creation date adjustment summary: "
        f"{changed_count} updated, {len(failed_results)} failed, {len(results)} matched file(s)",
        emoji="list",
    )

    for result in failed_results:
        print_error(f"Failed timestamp update: {result.source}: {result.error}")


def main() -> None:
    """Run the selected file creation date adjustment set."""

    config = load(__file__)
    ensure_section(config)
    set_name = selected_set_name(
        config,
        CONFIG_SECTION,
        label="file creation date adjustment set",
        header="Available file creation date adjustment sets:",
        empty_message=(
            "No file creation date adjustment sets configured in config.yaml. "
            f"Please add an '{CONFIG_SECTION}' section."
        ),
    )
    script_config = get_set_config(
        config,
        CONFIG_SECTION,
        set_name,
        label="File creation date adjustment set",
    )
    validate_script_config(script_config, set_name)

    print_start(f"Starting file creation date adjustment: {set_name}")
    connect_from_config(
        scoped_config_for_optional_smb(
            config,
            script_config,
            error_label=f"Configuration value '{config_key(set_name, 'smb')}'",
        ),
        on_password_prompted=lambda password: store_prompted_password(config, password),
    )

    results = adjust_file_creation_dates(script_config, set_name)
    summarize_adjustment_results(results)

    if any(result.failed for result in results):
        raise FileCreationDateAdjustmentError(results)

    changed_count = sum(1 for result in results if result.changed)
    print_done(f"File creation date adjustment finished: {set_name}: {changed_count} file(s) updated")


if __name__ == "__main__":
    main()
