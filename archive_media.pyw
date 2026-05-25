import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import config_support as config_loader
from winutils_python import connect_smb, visual


DEFAULT_SECTION = r'''archive_media:
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


@dataclass(frozen=True)
class ArchiveTaskResult:
    source: Path
    target: Path
    moved_count: int = 0
    error: Exception | None = None

    @property
    def failed(self) -> bool:
        return self.error is not None


class ArchiveMediaError(RuntimeError):
    def __init__(self, results: list[ArchiveTaskResult]) -> None:
        self.results = results
        failed_results = [result for result in results if result.failed]
        summary = ", ".join(
            f"{result.source} → {result.target}: {result.error}"
            for result in failed_results
        )
        super().__init__(f"{len(failed_results)} archive media task(s) failed: {summary}")


def store_prompted_smb_password(config: dict, password: str) -> None:
    config_path = config.get("__config_path__")
    if config_path is None:
        raise ValueError("Loaded configuration is missing internal '__config_path__'")

    config_loader.replace_or_add_string_value(config_path, "smb", "encrypted_password", connect_smb.encrypt_password(password))
    config_loader.remove_value(config_path, "smb", "password_file")
    config_loader.remove_value(config_path, "smb", "password")


def get_creation_time(path: Path) -> datetime:
    stat_result = path.stat()

    if hasattr(stat_result, "st_birthtime"):
        timestamp = stat_result.st_birthtime
    else:
        timestamp = stat_result.st_ctime

    return datetime.fromtimestamp(timestamp)


def remove_destination_if_exists(destination: Path) -> None:
    if not destination.exists() and not destination.is_symlink():
        return

    if destination.is_dir() and not destination.is_symlink():
        shutil.rmtree(destination)
    else:
        destination.unlink()


def move_media_to_dated_archive(source: Path, target: Path, extensions: set[str]) -> int:
    validate_archive_source(source)

    moved_count = 0
    if visual.is_terminal():
        print(flush=True)

    visual.print_info(f"Archiving media: {source} → {target}", emoji="archive")

    for current_path in source.rglob("*"):
        if not current_path.is_file():
            continue

        if current_path.suffix.lower() not in extensions:
            continue

        creation_date = get_creation_time(current_path)
        target_year_month_day = (
            target
            / creation_date.strftime("%Y")
            / creation_date.strftime("%m")
            / creation_date.strftime("%d")
        )
        target_year_month_day.mkdir(parents=True, exist_ok=True)

        destination = target_year_month_day / current_path.name
        remove_destination_if_exists(destination)
        shutil.move(str(current_path), str(destination))
        moved_count += 1
        visual.print_success(f"Moved: {current_path.name} → {destination}", emoji="move")

    visual.print_success(f"Archive task finished: {moved_count} file(s) moved")
    return moved_count


def get_archive_tasks(script_config: dict) -> tuple[tuple[Path, Path], ...]:
    return tuple(
        (Path(str(task["source"])), Path(str(task["target"])))
        for task in script_config.get("tasks", [])
    )


def validate_archive_source(source: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Archive source does not exist: {source}")

    if not source.is_dir():
        raise NotADirectoryError(f"Archive source is not a directory: {source}")


def get_media_extensions(script_config: dict) -> set[str]:
    extensions = script_config.get("extensions", [])

    if not isinstance(extensions, list):
        raise TypeError("Configuration value 'archive_media.extensions' must be a list")

    normalized_extensions = {str(extension).lower() for extension in extensions if str(extension).strip()}

    if not normalized_extensions:
        raise ValueError("Configuration value 'archive_media.extensions' must define at least one extension")

    return normalized_extensions


def ensure_section(config: dict) -> dict:
    if "archive_media" not in config:
        config_loader.append_section_yaml(config, DEFAULT_SECTION)
        visual.print_warning("Added default 'archive_media' section to config.yaml. Please configure it before running.")
        raise SystemExit(1)

    return config_loader.get_table(config, "archive_media")


def config_for_archive_smb(config: dict, script_config: dict) -> dict:
    archive_smb = script_config.get("smb", False)

    if not isinstance(archive_smb, bool):
        raise TypeError("Configuration value 'archive_media.smb' must be true or false")

    scoped_config = dict(config)

    if archive_smb:
        return scoped_config

    scoped_config.pop("smb", None)
    return scoped_config


def run_archive_tasks(tasks: tuple[tuple[Path, Path], ...], extensions: set[str]) -> list[ArchiveTaskResult]:
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
    config = config_loader.load(__file__)
    script_config = ensure_section(config)

    visual.print_start("Starting media archive")
    connect_smb.connect_from_config(
        config_for_archive_smb(config, script_config),
        on_password_prompted=lambda password: store_prompted_smb_password(config, password),
    )

    extensions = get_media_extensions(script_config)
    results = run_archive_tasks(get_archive_tasks(script_config), extensions)
    summarize_archive_results(results)

    if any(result.failed for result in results):
        raise ArchiveMediaError(results)

    total_moved = sum(result.moved_count for result in results)
    visual.print_done(f"Media archive finished: {total_moved} file(s) moved")


if __name__ == "__main__":
    main()
