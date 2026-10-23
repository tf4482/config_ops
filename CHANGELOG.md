# 📝 Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and [Semantic Versioning](https://semver.org/).

## [0.1.0] - DATE

### Added

- Single `config_ops` binary with subcommands `smb`, `files`, `archive`, `dates`, `ssh`, and `peripherals`.
- Strict `config.yml` with first-run template, placeholder rejection, and lookup beside the executable or in `~/.config/config_ops/`.
- Arrow-key menus for tasks and sets on a terminal, including double-click starts.
- `src` layout with the shared house frame, PyInstaller spec, ruff, pyright, and pre-commit setup.

### Changed

- Merged the former `winutils_python` submodule and the six scripts into one package without duplicated code.
- Replaced the `.pyw` scripts, `config.yaml`, and the obfuscated SMB password with `config.yml` and `smb.password`.
- Flattened Robocopy groups into a `tasks` list with a `type` per task.

### Removed

- GUI-friendly `.pyw` launchers, and the `VERSION` file; the version lives in `pyproject.toml`.
