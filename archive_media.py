"""Archive configured media files into date-based folders.

The script reads a named ``archive_media`` configuration set, optionally connects
SMB mappings, and moves files with configured extensions into ``YYYY/MM/DD``
folders based on filesystem creation time.
"""

import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from winutils_python import connect_smb, menu, visual

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


def app_dir(script_file: str | Path) -> Path:
    """Return the directory that should contain config.yaml.

    In a normal Python run this is the script directory.
    In a PyInstaller .exe this is the .exe directory, not the temporary
    extraction directory used by --onefile builds.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(script_file).resolve().parent


def script_dir(script_file: str | Path) -> Path:
    """Return the directory used for script-local runtime files."""

    return app_dir(script_file)


def config_path(script_file: str | Path) -> Path:
    """Return the expected ``config.yaml`` path for this script."""

    return script_dir(script_file) / "config.yaml"


def find_config_path(script_file: str | Path) -> Path:
    """Return the config path, creating an empty config file when missing."""

    path = config_path(script_file)

    if not path.exists():
        path.write_text("", encoding="utf-8")

    return path


def load_config(script_file: str | Path) -> dict[str, Any]:
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


def config_key(set_name: str, name: str) -> str:
    """Return a dotted config key for human-readable error messages."""

    return f"{CONFIG_SECTION}.{set_name}.{name}"


def get_archive_sets(config: dict[str, Any]) -> dict[str, Any]:
    """Return all configured media archive sets."""

    return get_table(config, CONFIG_SECTION)


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


def validate_archive_set_name(config: dict[str, Any], set_name: str) -> str:
    """Validate a requested archive set name and return it unchanged."""

    archive_sets = get_archive_sets(config)

    if set_name not in archive_sets:
        available_sets = ", ".join(archive_sets) or "none"
        raise SystemExit(f"Unknown archive media set '{set_name}'. Available sets: {available_sets}")

    return set_name


def archive_set_config(config: dict[str, Any], set_name: str) -> dict[str, Any]:
    """Return the configuration table for a named archive set."""

    archive_sets = get_archive_sets(config)
    archive_set = archive_sets.get(set_name)

    if not isinstance(archive_set, dict):
        raise TypeError(f"Archive media set '{set_name}' must be a table")

    return archive_set


def choose_archive_set_terminal(config: dict[str, Any]) -> str:
    """Prompt the user to choose an archive set from the terminal."""

    archive_sets = get_archive_sets(config)
    return menu.choose_mapping_key_terminal(
        archive_sets,
        header="Available archive media sets:",
        empty_message=f"No archive media sets configured in config.yaml. Please add an '{CONFIG_SECTION}' section.",
    )


def archive_set_name(config: dict[str, Any]) -> str:
    """Return the selected archive set from CLI args or the terminal menu."""

    if len(sys.argv) > 1:
        return validate_archive_set_name(config, menu.normalize_selection_name(sys.argv[1]))

    return choose_archive_set_terminal(config)


def get_creation_time(path: Path) -> datetime:
    """Return the filesystem creation time for a path as local datetime."""

    stat_result = path.stat()

    if hasattr(stat_result, "st_birthtime"):
        timestamp = stat_result.st_birthtime
    else:
        timestamp = stat_result.st_ctime

    return datetime.fromtimestamp(timestamp)


def remove_destination_if_exists(destination: Path) -> None:
    """Delete an existing destination file, symlink, or directory."""

    if not destination.exists() and not destination.is_symlink():
        return

    if destination.is_dir() and not destination.is_symlink():
        shutil.rmtree(destination)
    else:
        destination.unlink()


def move_media_to_dated_archive(source: Path, target: Path, extensions: set[str]) -> int:
    """Move matching media files from one source into dated archive folders."""

    validate_archive_source(source)

    moved_count = 0

    visual.print_info(f"Archiving media: {source} → {target}", emoji="archive")

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
        visual.print_success(f"Moved: {current_path.name} → {destination}", emoji="move")

    visual.print_success(f"Archive task finished: {moved_count} file(s) moved")
    return moved_count


def dated_archive_folder(target: Path, creation_date: datetime) -> Path:
    """Return the ``YYYY/MM/DD`` archive folder for a creation date."""

    return target / creation_date.strftime("%Y") / creation_date.strftime("%m") / creation_date.strftime("%d")


def get_archive_tasks(script_config: dict[str, Any], set_name: str) -> tuple[tuple[Path, Path], ...]:
    """Return configured archive task source and target path pairs."""

    tasks = script_config.get("tasks", [])

    if not isinstance(tasks, list):
        raise TypeError(f"Configuration value '{config_key(set_name, 'tasks')}' must be a list")

    return tuple((Path(str(task["source"])), Path(str(task["target"]))) for task in tasks)


def validate_archive_source(source: Path) -> None:
    """Ensure an archive source exists and is a directory."""

    if not source.exists():
        raise FileNotFoundError(f"Archive source does not exist: {source}")

    if not source.is_dir():
        raise NotADirectoryError(f"Archive source is not a directory: {source}")


def get_media_extensions(script_config: dict[str, Any], set_name: str) -> set[str]:
    """Return normalized lowercase media extensions for a set."""

    extensions = script_config.get("extensions", [])

    if not isinstance(extensions, list):
        raise TypeError(f"Configuration value '{config_key(set_name, 'extensions')}' must be a list")

    normalized_extensions = {str(extension).lower() for extension in extensions if str(extension).strip()}

    if not normalized_extensions:
        raise ValueError(f"Configuration value '{config_key(set_name, 'extensions')}' must define at least one extension")

    return normalized_extensions


def ensure_section(config: dict[str, Any]) -> None:
    """Ensure the archive section exists and has table shape."""

    if CONFIG_SECTION not in config:
        append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning(
            f"Added default '{CONFIG_SECTION}' section to config.yaml. Please configure it before running."
        )
        raise SystemExit(1)

    get_archive_sets(config)


def config_for_archive_smb(config: dict[str, Any], script_config: dict[str, Any], set_name: str) -> dict[str, Any]:
    """Return the scoped config used for optional SMB connection setup."""

    archive_smb = script_config.get("smb", False)

    if not isinstance(archive_smb, bool):
        raise TypeError(f"Configuration value '{config_key(set_name, 'smb')}' must be true or false")

    scoped_config = dict(config)

    if archive_smb:
        return scoped_config

    scoped_config.pop("smb", None)
    return scoped_config


def run_archive_tasks(tasks: tuple[tuple[Path, Path], ...], extensions: set[str]) -> list[ArchiveTaskResult]:
    """Run archive tasks and collect successes and failures."""

    results: list[ArchiveTaskResult] = []

    for source, target in tasks:
        try:
            moved_count = move_media_to_dated_archive(source, target, extensions)
            results.append(ArchiveTaskResult(source, target, moved_count=moved_count))
        except Exception as error:
            visual.print_error(f"Archive task failed: {source} → {target}: {error}")
            results.append(ArchiveTaskResult(source, target, error=error))

    return results


def summarize_archive_results(results: list[ArchiveTaskResult]) -> None:
    """Print a compact summary of archive task results."""

    failed_results = [result for result in results if result.failed]
    successful_count = len(results) - len(failed_results)
    total_moved = sum(result.moved_count for result in results)

    visual.print_info(
        f"Media archive summary: {successful_count} succeeded, {len(failed_results)} failed, "
        f"{len(results)} total, {total_moved} file(s) moved",
        emoji="list",
    )

    for result in failed_results:
        visual.print_error(f"Failed archive task: {result.source} → {result.target}: {result.error}")


def main() -> None:
    """Run the selected media archive set."""

    config = load_config(__file__)
    ensure_section(config)
    set_name = archive_set_name(config)
    script_config = archive_set_config(config, set_name)

    visual.print_start(f"Starting media archive: {set_name}")
    connect_smb.connect_from_config(
        config_for_archive_smb(config, script_config, set_name),
        on_password_prompted=lambda password: connect_smb.store_prompted_password(config, password),
    )

    extensions = get_media_extensions(script_config, set_name)
    results = run_archive_tasks(get_archive_tasks(script_config, set_name), extensions)
    summarize_archive_results(results)

    if any(result.failed for result in results):
        raise ArchiveMediaError(results)

    total_moved = sum(result.moved_count for result in results)
    visual.print_done(f"Media archive finished: {set_name}: {total_moved} file(s) moved")


if __name__ == "__main__":
    main()
