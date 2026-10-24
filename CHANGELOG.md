# 📝 Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and [Semantic Versioning](https://semver.org/).

## [2.1.0] - 2026-10-24

### Added

- Task type `mirror-file` for `files` sets: mirrors a single file to a target path.
- Several targets (a list) for `mirror` and `mirror-file` tasks.

### Changed

- Tasks on an SMB share that cannot be reached are skipped instead of failing the run; they are reported at the end.

## [2.0.0] - 2026-10-23

### Added

- Single `config_ops` binary with subcommands `smb`, `files`, `archive`, `dates`, and `ssh`.
- Strict `config.yml` with first-run template, placeholder rejection, and lookup in `~/.config/config_ops/` first, then beside the executable.
- Compact, colored Robocopy results with a progress bar instead of the raw summary tables.
- Arrow-key menus for tasks and sets on a terminal, including double-click starts.
- `src` layout with the shared house frame, PyInstaller spec, ruff, pyright, and pre-commit setup.

### Changed

- **Breaking:** one binary with subcommands (`config_ops files <set>`) and a strict `config.yml` replace the six scripts and `config.yaml`; existing calls and configuration files must be updated.
- Updated the shared frame: progress bar with percentage and gradient, `ssh.timeout_seconds` capped at one day.
- Merged the former `winutils_python` submodule and the six scripts into one package without duplicated code.
- Replaced the `.pyw` scripts, `config.yaml`, and the obfuscated SMB password with `config.yml` and an optional `smb.password_file` (default `~/.config/.smbcredentials`).
- Flattened Robocopy groups into a `tasks` list with a `type` per task.

### Removed

- The `peripherals` tool and its configuration section.
- GUI-friendly `.pyw` launchers, and the `VERSION` file; the version lives in `pyproject.toml`.
