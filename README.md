# Config Ops ⚙️

Configuration-driven Windows utilities for recurring file, network, media, SSH, and peripheral tasks. Every tool reads named sets from one `config.yaml`, validates them, and reports progress with colorful output.

## Features ✨

- **File operations** 📂 (`file_operations.py`): Named Robocopy sets with `mirror`, `copy`, `move`, and bidirectional `sync`.
- **Media archive** 🗃️ (`archive_media.py`): Moves media recursively into `YYYY/MM/DD` folders by creation date.
- **Timestamp adjustment** 🕒 (`adjust_file_creation_date.py`): Sets Windows file times from filenames, folder names, or EXIF/QuickTime metadata; reverse mode writes modification dates into metadata.
- **SMB connections** 🔌 (`connect_smb.pyw`): Connects shares as drive mappings or direct UNC sessions; prompted passwords are stored obfuscated.
- **SSH tasks** 🔐 (`ssh_tasks.py`): Runs named non-interactive SSH commands with optional timeouts.
- **Peripherals** 💡 (`peripherals.pyw`): Triggers URL-controlled devices (`on`, `off`, `toggle`, `suspend`, `resume`) and keeps their state in the registry.
- **Deployment** 📦 (`deploy.py`): Builds all scripts as executables and copies them locally and optionally to a remote host.

## Prerequisites 📋

- Windows with Python 3.13 or newer
- [uv](https://docs.astral.sh/uv/) and Git
- `robocopy`, `net use`, `curl.exe`, and OpenSSH `ssh` (plus `scp` for remote deployment), depending on the tool

## Installation 🚀

1. Clone the repository:

   ```powershell
   git clone <repository-url> config_ops
   cd config_ops
   ```

2. Install the dependencies:

   ```powershell
   uv sync
   ```

3. Run a tool once. It creates `config.yaml`, appends an example for its missing section, and exits. Edit the values and run it again.

## Configuration ⚙️

`config.yaml` lives next to the script (or the executable when frozen) and is not tracked by Git. Named tools take a set name as first argument; without one, an interactive menu appears. Unattended runs must pass the set name.

| Section | Used by | Purpose |
| --- | --- | --- |
| `smb` | all tools with `smb: true` | User, stored password, and share mappings |
| `file_operations` | `file_operations.py` | Sets with `mirror`, `copy`, `move`, `sync`, `robocopy` options |
| `archive_media` | `archive_media.py` | Sets with `extensions` and `tasks` (`source`, `target`) |
| `adjust_file_creation_date` | `adjust_file_creation_date.py` | Sets with `mode`, folders, `extensions`, `patterns`, `hour_adjustment` |
| `ssh` | `ssh_tasks.py` | Sets with `user`, `host`, `port`, `timeout`, `command` |
| `peripherals` | `peripherals.pyw` | `registry_path` plus devices with `on`/`off` URLs |

```yaml
smb:
  user: 'DOMAIN\user'
  mappings:
    - share: '\\server\shared'         # direct UNC session
    - drive: 'R:'                      # persistent drive mapping
      share: '\\server\backup'

file_operations:
  backup:
    smb: true
    mirror:
      - source: 'C:\data'
        target: 'R:\data'
    sync:
      - source: 'C:\notes'
        target: 'R:\notes'

archive_media:
  phone_photos:
    smb: true
    extensions: ['.jpg', '.png', '.mp4']
    tasks:
      - source: 'C:\phone-import'
        target: 'R:\photos'

adjust_file_creation_date:
  screenshots:
    mode: file                         # file | folder | metadata | metadata_reverse
    source_folder: 'C:\screenshots'
    target_folder: ''
    extensions: ['.jpg', '.png']
    change_files_in_place: true
    overwrite: true
    hour_adjustment: 0
    patterns:
      - pattern: '^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})'

ssh:
  remote_backup:
    user: 'user'
    host: '192.0.2.12'
    port: 22
    timeout: 300
    command: 'manual_backup.bash'

peripherals:
  registry_path: 'Software\peripherals'
  led:
    'on': 'https://example.invalid/led/on'
    'off': 'https://example.invalid/led/off'
```

### Behavior notes 📝

- **SMB**: `smb: true` in a set uses the top-level `smb` section. Without a stored password, interactive runs prompt once; unattended runs fail fast.
- **Mirror** can delete extra target content. **Sync** keeps newer files, never purges, and rejects identical or nested folders. Robocopy exit codes below `8` count as success.
- **Archive** replaces an existing destination file; the target must not be inside the source.
- **Timestamp modes**: `file` (filename, source folder only), `folder` (folder name, recursive), `metadata` (EXIF/QuickTime, recursive), `metadata_reverse` (modification time into metadata, recursive).

## Usage 🖥️

Start a tool with a set name, or without one for the menu:

```powershell
uv run python file_operations.py backup
uv run python archive_media.py phone_photos
uv run python adjust_file_creation_date.py screenshots
uv run python ssh_tasks.py remote_backup
uv run python connect_smb.pyw
uv run python peripherals.pyw led on
```

### Examples

```powershell
# Back up and sync folders to a network share
uv run python file_operations.py backup

# Toggle all devices, or only specific ones
uv run python peripherals.pyw
uv run python peripherals.pyw led boxen off

# Pick a set interactively
uv run python archive_media.py
```

Each run prints start, progress, and a summary; any failure ends with a non-zero exit code, so the tools work in scheduled tasks.

## Project Structure 📁

| Path | Purpose |
| --- | --- |
| `file_operations.py`, `archive_media.py`, `adjust_file_creation_date.py`, `ssh_tasks.py` | Set-based task scripts (each is self-contained) |
| `connect_smb.pyw`, `peripherals.pyw` | Windowless helper scripts |
| `deploy.py` | Build and copy executables (local only, not tracked) |
| `tests/` | Automated tests |
| `config.yaml` | Local configuration (not tracked) |
| `LICENSE` | License terms |

## Development 🔧

Run the tests:

```powershell
uv run pytest
```

Build and deploy all scripts as executables to `~/bin` and the configured remote host:

```powershell
uv run python deploy.py
```

## Contributing 🤝

Contributions are welcome! Open an issue to report a bug or suggest an improvement.

To contribute code:

1. Fork the repository and create a branch.
2. Make your changes and update relevant tests and documentation.
3. Run the available checks.
4. Submit a pull request describing your changes.

## License 📜

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
