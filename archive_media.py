"""Archive configured media files into date-based folders.

The script reads a named ``archive_media`` configuration set, optionally connects
SMB mappings, and moves files with configured extensions into ``YYYY/MM/DD``
folders based on filesystem creation time.
"""

import base64
import getpass
import hashlib
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
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


def require_list_item_keys(
    items: list[Any],
    item_label: str,
    required_keys: Iterable[RequiredKey],
) -> None:
    """Validate required keys for every table in a config list."""

    missing: list[str] = []

    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            missing.append(f"{item_label}[{index}] must be a table")
            continue

        item_missing = missing_keys(
            item,
            (
                required_key(
                    required.key,
                    label=required.label or f"{item_label}[{index}].{required.key}",
                    allow_empty=required.allow_empty,
                )
                for required in required_keys
            ),
        )
        missing.extend(item_missing)

    report_missing_keys(missing)

    if missing:
        raise SystemExit(1)


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



CONFIG_SECTION = "archive_media"

DEFAULT_SECTION = r'''archive_media:
  example_set:
    smb: false
    extensions:
      - .jpg
      - .jpeg
      - .png
      - .gif
      - .bmp
    tasks:
      - source: 'R:\path\to\source'
        target: 'R:\path\to\target'
'''

def config_key(set_name: str, name: str) -> str:
    """Return a dotted config key for human-readable error messages."""

    return config_sets_config_key(CONFIG_SECTION, set_name, name)


@dataclass(frozen=True)
class ArchiveTaskResult:
    """Result for one configured media archive task."""

    source: Path
    target: Path
    moved_count: int = 0
    error: Exception | None = None

    @property
    def failed(self) -> bool:
        """Return whether this archive task failed."""

        return self.error is not None


class ArchiveMediaError(RuntimeError):
    """Raised after processing when one or more archive tasks failed."""

    def __init__(self, results: list[ArchiveTaskResult]) -> None:
        """Build a summary error from failed archive task results."""

        self.results = results
        failed_results = [result for result in results if result.failed]
        summary = ", ".join(
            f"{result.source} → {result.target}: {result.error}"
            for result in failed_results
        )
        super().__init__(f"{len(failed_results)} archive media task(s) failed: {summary}")


def get_creation_time(path: Path) -> datetime:
    """Return the filesystem creation time for a path as local datetime."""

    stat_result = path.stat()

    if hasattr(stat_result, "st_birthtime"):
        timestamp = stat_result.st_birthtime
    else:
        timestamp = stat_result.st_ctime

    return datetime.fromtimestamp(timestamp).astimezone()


def remove_destination_if_exists(destination: Path) -> None:
    """Delete an existing destination file or symlink."""

    if not destination.exists() and not destination.is_symlink():
        return

    if destination.is_dir() and not destination.is_symlink():
        raise IsADirectoryError(f"Archive destination is an existing directory: {destination}")

    destination.unlink()


def move_media_to_dated_archive(source: Path, target: Path, extensions: set[str]) -> int:
    """Move matching media files from one source into dated archive folders."""

    validate_archive_paths(source, target)

    moved_count = 0

    print_info(f"Archiving media: {source} → {target}", emoji="archive")

    for current_path in source.rglob("*"):
        if not current_path.is_file():
            continue

        if current_path.suffix.lower() not in extensions:
            continue

        creation_date = get_creation_time(current_path)
        target_year_month_day = dated_archive_folder(target, creation_date)
        target_year_month_day.mkdir(parents=True, exist_ok=True)

        destination = target_year_month_day / current_path.name
        remove_destination_if_exists(destination)
        shutil.move(str(current_path), str(destination))
        moved_count += 1
        print_success(f"Moved: {current_path.name} → {destination}", emoji="move")

    print_success(f"Archive task finished: {moved_count} file(s) moved")
    return moved_count


def dated_archive_folder(target: Path, creation_date: datetime) -> Path:
    """Return the ``YYYY/MM/DD`` archive folder for a creation date."""

    return target / creation_date.strftime("%Y") / creation_date.strftime("%m") / creation_date.strftime("%d")


def get_archive_tasks(script_config: dict[str, Any], set_name: str) -> tuple[tuple[Path, Path], ...]:
    """Return configured archive task source and target path pairs."""

    tasks = required_list(script_config, "tasks", label=config_key(set_name, "tasks"))
    require_list_item_keys(
        tasks,
        config_key(set_name, "tasks"),
        (
            required_key("source"),
            required_key("target"),
        ),
    )
    return tuple((Path(str(task["source"])), Path(str(task["target"]))) for task in tasks)


def validate_script_config(script_config: dict[str, Any], set_name: str) -> None:
    """Report missing required configuration for one archive media set."""

    require_set_keys(
        script_config,
        CONFIG_SECTION,
        set_name,
        (
            required_key("extensions"),
            required_key("tasks"),
        ),
    )


def validate_archive_source(source: Path) -> None:
    """Ensure an archive source exists and is a directory."""

    if not source.exists():
        raise FileNotFoundError(f"Archive source does not exist: {source}")

    if not source.is_dir():
        raise NotADirectoryError(f"Archive source is not a directory: {source}")


def validate_archive_paths(source: Path, target: Path) -> None:
    """Reject archive targets that could be traversed from the source."""

    validate_archive_source(source)
    source_path = source.resolve()
    target_path = target.resolve()

    if target_path == source_path or source_path in target_path.parents:
        raise ValueError(f"Archive target must not be the source or a folder below it: {source} → {target}")


def get_media_extensions(script_config: dict[str, Any], set_name: str) -> set[str]:
    """Return normalized lowercase media extensions for a set."""

    return normalized_extension_set(
        script_config,
        "extensions",
        label=config_key(set_name, "extensions"),
    )


def ensure_section(config: dict[str, Any]) -> None:
    """Ensure the archive section exists and has table shape."""

    if CONFIG_SECTION not in config:
        append_section_yaml(config, DEFAULT_SECTION)
        print_warning(
            f"Added default '{CONFIG_SECTION}' section to config.yaml. Please configure it before running."
        )
        raise SystemExit(1)

    section_sets(config, CONFIG_SECTION)


def run_archive_tasks(tasks: tuple[tuple[Path, Path], ...], extensions: set[str]) -> list[ArchiveTaskResult]:
    """Run archive tasks and collect successes and failures."""

    results: list[ArchiveTaskResult] = []

    for source, target in tasks:
        try:
            moved_count = move_media_to_dated_archive(source, target, extensions)
            results.append(ArchiveTaskResult(source, target, moved_count=moved_count))
        except Exception as error:  # noqa: BLE001
            print_error(f"Archive task failed: {source} → {target}: {error}")
            results.append(ArchiveTaskResult(source, target, error=error))

    return results


def summarize_archive_results(results: list[ArchiveTaskResult]) -> None:
    """Print a compact summary of archive task results."""

    failed_results = [result for result in results if result.failed]
    successful_count = len(results) - len(failed_results)
    total_moved = sum(result.moved_count for result in results)

    print_info(
        f"Media archive summary: {successful_count} succeeded, {len(failed_results)} failed, "
        f"{len(results)} total, {total_moved} file(s) moved",
        emoji="list",
    )

    for result in failed_results:
        print_error(f"Failed archive task: {result.source} → {result.target}: {result.error}")


def main() -> None:
    """Run the selected media archive set."""

    config = load(__file__)
    ensure_section(config)
    set_name = selected_set_name(
        config,
        CONFIG_SECTION,
        label="archive media set",
        header="Available archive media sets:",
        empty_message=f"No archive media sets configured in config.yaml. Please add an '{CONFIG_SECTION}' section.",
    )
    script_config = get_set_config(config, CONFIG_SECTION, set_name, label="Archive media set")
    validate_script_config(script_config, set_name)

    print_start(f"Starting media archive: {set_name}")
    connect_from_config(
        scoped_config_for_optional_smb(
            config,
            script_config,
            error_label=f"Configuration value '{config_key(set_name, 'smb')}'",
        ),
        on_password_prompted=lambda password: store_prompted_password(config, password),
    )

    extensions = get_media_extensions(script_config, set_name)
    results = run_archive_tasks(get_archive_tasks(script_config, set_name), extensions)
    summarize_archive_results(results)

    if any(result.failed for result in results):
        raise ArchiveMediaError(results)

    total_moved = sum(result.moved_count for result in results)
    print_done(f"Media archive finished: {set_name}: {total_moved} file(s) moved")


if __name__ == "__main__":
    main()
