"""Archive configured media files into date-based folders.

The script reads a named ``archive_media`` configuration set, optionally connects
SMB mappings, and moves files with configured extensions into ``YYYY/MM/DD``
folders based on filesystem creation time.
"""

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from winutils_python import config as config_utils
from winutils_python import config_validation
from winutils_python import config_sets, connect_smb, visual

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

    return config_sets.config_key(CONFIG_SECTION, set_name, name)


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

    tasks = config_utils.required_list(script_config, "tasks", label=config_key(set_name, "tasks"))
    config_validation.require_list_item_keys(
        tasks,
        config_key(set_name, "tasks"),
        (
            config_validation.required_key("source"),
            config_validation.required_key("target"),
        ),
    )
    return tuple((Path(str(task["source"])), Path(str(task["target"]))) for task in tasks)


def validate_script_config(script_config: dict[str, Any], set_name: str) -> None:
    """Report missing required configuration for one archive media set."""

    config_validation.require_set_keys(
        script_config,
        CONFIG_SECTION,
        set_name,
        (
            config_validation.required_key("extensions"),
            config_validation.required_key("tasks"),
        ),
    )


def validate_archive_source(source: Path) -> None:
    """Ensure an archive source exists and is a directory."""

    if not source.exists():
        raise FileNotFoundError(f"Archive source does not exist: {source}")

    if not source.is_dir():
        raise NotADirectoryError(f"Archive source is not a directory: {source}")


def get_media_extensions(script_config: dict[str, Any], set_name: str) -> set[str]:
    """Return normalized lowercase media extensions for a set."""

    return config_utils.normalized_extension_set(
        script_config,
        "extensions",
        label=config_key(set_name, "extensions"),
    )


def ensure_section(config: dict[str, Any]) -> None:
    """Ensure the archive section exists and has table shape."""

    if CONFIG_SECTION not in config:
        config_utils.append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning(
            f"Added default '{CONFIG_SECTION}' section to config.yaml. Please configure it before running."
        )
        raise SystemExit(1)

    config_sets.section_sets(config, CONFIG_SECTION)


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

    config = config_utils.load(__file__)
    ensure_section(config)
    set_name = config_sets.selected_set_name(
        config,
        CONFIG_SECTION,
        label="archive media set",
        header="Available archive media sets:",
        empty_message=f"No archive media sets configured in config.yaml. Please add an '{CONFIG_SECTION}' section.",
    )
    script_config = config_sets.get_set_config(config, CONFIG_SECTION, set_name, label="Archive media set")
    validate_script_config(script_config, set_name)

    visual.print_start(f"Starting media archive: {set_name}")
    connect_smb.connect_from_config(
        connect_smb.scoped_config_for_optional_smb(
            config,
            script_config,
            error_label=f"Configuration value '{config_key(set_name, 'smb')}'",
        ),
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
