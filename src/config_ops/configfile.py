# Shared configuration frame: lookup, first-run template, strict validation helpers.
# Copy unchanged; the app supplies DEFAULT_CONFIG, Config, and parse_config in config.py.
# Project deviation: the user directory is searched before the executable directory.
from __future__ import annotations

import math
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

PROJECT_NAME = __package__ or Path(sys.argv[0]).stem
CONFIG_FILENAME = "config.yml"
PLACEHOLDER = "CHANGE_ME"


# Invalid or unreadable configuration
class ConfigError(ValueError):
    pass


# Signal for a freshly written template
class ConfigCreated(Exception):
    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(str(path))


# Directory of the launched script or frozen executable, symlinks resolved
def launcher_dir() -> Path:
    launcher = sys.executable if getattr(sys, "frozen", False) else sys.argv[0]
    return Path(launcher or sys.executable).resolve().parent


# First existing file wins; no merging
def load_config[T](
    default_config: str,
    parse: Callable[[Any], T],
    *,
    executable_dir: Path | None = None,
    home: Path | None = None,
) -> tuple[Path, T]:
    user_path = (home or Path.home()) / ".config" / PROJECT_NAME / CONFIG_FILENAME
    candidates = (user_path, (executable_dir or launcher_dir()) / CONFIG_FILENAME)
    selected = next((path for path in candidates if path.is_file()), None)
    if selected is None:
        _create_default_config(user_path, default_config)
        raise ConfigCreated(user_path)

    try:
        document = yaml.safe_load(selected.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        # Line number only; never echoes file content
        mark = getattr(error, "problem_mark", None)
        location = f" at line {mark.line + 1}" if mark else ""
        raise ConfigError(f"invalid YAML in {selected}{location}") from None
    except (OSError, UnicodeError) as error:
        raise ConfigError(f"cannot read configuration {selected}: {error}") from error
    return selected, parse(document)


# User-only template with dummy values
def _create_default_config(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(content)
    except FileExistsError:
        raise ConfigError(f"configuration appeared while creating it: {path}") from None
    except OSError as error:
        raise ConfigError(f"cannot create configuration {path}: {error}") from error


def mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{location} must be a YAML mapping")
    return value


# Reject missing and unknown keys
def exact_keys(values: Mapping[str, Any], expected: set[str], location: str) -> None:
    details = []
    if missing := expected - values.keys():
        details.append(f"missing: {', '.join(sorted(missing))}")
    if unknown := values.keys() - expected:
        details.append(f"unknown: {', '.join(sorted(map(str, unknown)))}")
    if details:
        raise ConfigError(f"{location} keys are invalid ({'; '.join(details)})")


# Trimmed non-empty string without placeholder
def text(values: Mapping[str, Any], key: str, location: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location}.{key} must be a non-empty string")
    if value.strip() == PLACEHOLDER:
        raise ConfigError(f"edit placeholder value at {location}.{key}")
    return value.strip()


def port(values: Mapping[str, Any], key: str, location: str) -> int:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ConfigError(f"{location}.{key} must be an integer from 1 to 65535")
    return value


def positive_int(values: Mapping[str, Any], key: str, location: str) -> int:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{location}.{key} must be a positive integer")
    return value


# Positive finite seconds as float
def positive_seconds(values: Mapping[str, Any], key: str, location: str) -> float:
    value = values.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ConfigError(f"{location}.{key} must be a positive number")
    return float(value)
