# ⚙️ Config Ops

> Configuration-driven Windows utilities for recurring file, network, media, SSH, and peripheral tasks.

Config Ops is a script-first collection of automations backed by one `config.yaml`. The task scripts validate their configuration, print colorful progress and error summaries, and use the local `winutils_python` submodule for shared Windows operations.

## ✨ Features

| Tool | Purpose |
| --- | --- |
| `file_operations.py` | Run named Robocopy `mirror`, `copy`, `move`, and bidirectional `sync` jobs. |
| `archive_media.py` | Move media recursively into `YYYY/MM/DD` folders using file creation dates. |
| `adjust_file_creation_date.py` | Read timestamps from filenames, folder names, or media metadata and update Windows file times; reverse mode writes file modification dates into media metadata. |
| `connect_smb.pyw` | Connect SMB shares as drive mappings or direct UNC sessions without opening a console window. |
| `ssh_tasks.py` | Run named non-interactive SSH commands with optional timeouts. |
| `peripherals.pyw` | Trigger URL-controlled devices and persist their state in the current user's registry. |
| `deploy.py` | Build all task scripts as executables and copy them to local and optional remote targets. |

## ✅ Requirements

- Windows
- Python 3.13 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Git with submodule support
- Windows tools used by individual features: `robocopy`, `net use`, `curl.exe`, and OpenSSH `ssh` (plus `scp` for remote deployment)

## 🚀 Install

```powershell
git clone --recurse-submodules <repository-url> config_ops
cd config_ops
uv sync
```

For an existing clone:

```powershell
git submodule update --init --recursive
uv sync
```

`uv sync` installs runtime and development dependencies, including the editable `winutils_python` submodule.

## ⚙️ Configure

Task scripts read `config.yaml` from the directory containing the script—or the executable when frozen. On first run, a script creates the file if needed, appends an example for its missing section, and exits. Edit the generated values before running it again.

Named tools accept a set name as their first argument. Without one, an interactive terminal menu is shown. Non-interactive runs must provide the set name.

```powershell
uv run python file_operations.py backup
uv run python archive_media.py phone_photos
uv run python adjust_file_creation_date.py screenshots
uv run python ssh_tasks.py remote_backup
uv run python connect_smb.pyw
```

Example configuration covering every tool:

```yaml
smb:
  user: 'DOMAIN\user'
  mappings:
    - share: '\\server\shared'
    - drive: 'R:'
      share: '\\server\backup'

file_operations:
  backup:
    smb: true
    robocopy:
      common_options: ['/MT:32', '/W:2', '/R:10', '/XJ', '/ETA', '/TEE']
    mirror:
      - source: 'C:\data'
        target: 'R:\data'
    copy:
      overwrite: false
      tasks:
        - source: 'C:\documents'
          target: 'R:\documents'
    move:
      - source: 'C:\outbox'
        target: 'R:\inbox'
    sync:
      - source: 'C:\notes'
        target: 'R:\notes'

archive_media:
  phone_photos:
    smb: true
    extensions: ['.jpg', '.jpeg', '.png', '.mp4', '.mov']
    tasks:
      - source: 'C:\phone-import'
        target: 'R:\photos'

adjust_file_creation_date:
  screenshots:
    mode: file
    source_folder: 'C:\screenshots'
    target_folder: 'C:\screenshots-adjusted'
    extensions: ['.jpg', '.png']
    change_files_in_place: false
    overwrite: false
    hour_adjustment: 0
    patterns:
      - pattern: '^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})'

ssh:
  remote_backup:
    user: 'user'
    host: '192.168.1.12'
    port: 22
    timeout: 300
    command: 'manual_backup.bash'

peripherals:
  registry_path: 'Software\peripherals'
  led:
    'on': 'https://example.invalid/led/on'
    'off': 'https://example.invalid/led/off'
```

### SMB behavior

- Set `smb: true` inside a file, archive, or timestamp set to use the top-level SMB configuration; omit it or use `false` for local-only work.
- A mapping with `drive` creates a persistent drive mapping. Omitting `drive` connects the UNC share directly for paths such as `\\server\shared\folder`.
- If no stored password is available, an interactive run prompts for it and stores an obfuscated value in `config.yaml`. Unattended runs cannot prompt and fail fast.

### File operation behavior

- `mirror` makes the target match the source and can delete extra target content.
- `copy` recursively copies source content.
- `move` recursively transfers files and removes transferred source files.
- `sync` runs safe Robocopy passes in both directions, keeps newer versions, never purges, and rejects identical or nested folder pairs.
- Operation groups may be a direct task list or a table with `tasks`, `overwrite`, and `options`. Robocopy defaults can also be set through `robocopy.common_options` or type-specific options.
- Robocopy exit codes below `8` are successful; failures are summarized after the configured operations run.

### Media archive behavior

Archive tasks scan source folders recursively, move matching files into creation-date folders, and continue to later tasks after a failure. An existing destination file is replaced, but an existing directory at the destination is rejected. The target cannot equal or be inside the source.

### Timestamp modes

| Mode | Timestamp source | Scan |
| --- | --- | --- |
| `file` | Named regex groups in each filename | Source folder only |
| `folder` | Named regex groups in each containing folder name | Recursive |
| `metadata` | EXIF image or QuickTime-family video metadata | Recursive |
| `metadata_reverse` | Windows modification time written into media metadata | Recursive |

Pattern modes require `year` or `year2`, plus `month` and `day` named groups; `hour`, `minute`, and `second` are optional. Supported metadata formats are JPEG/TIFF images and MP4/QuickTime-family videos. Use `change_files_in_place: false` to copy files before changing them; copied folder and metadata workflows preserve the source hierarchy and create collision-safe names unless `overwrite` is enabled.

### Peripheral commands

```powershell
uv run python peripherals.pyw
uv run python peripherals.pyw led on
uv run python peripherals.pyw led toggle
uv run python peripherals.pyw suspend
uv run python peripherals.pyw resume
```

Commands are `on`, `off`, `toggle`, `suspend`, and `resume`. Omitting a device targets all configured devices; omitting a command uses `toggle`. State is stored below `HKEY_CURRENT_USER` at `peripherals.registry_path`.

## 🧪 Test

```powershell
uv run pytest
```

The regression suite covers unattended execution, direct UNC SMB sessions, Robocopy paths, strict configuration types, archive safety, SSH batch mode, and peripheral state updates.

## 📦 Deploy

`deploy.py` builds every root `.py` and `.pyw` task script with PyInstaller, copies the executables to `%USERPROFILE%\bin`, and attempts a remote copy over SSH/SCP. Set `REMOTE_USER`, `REMOTE_HOST`, and `REMOTE_PORT` near the top of the script for your target, then run:

```powershell
uv run python deploy.py
```

The deploy script uses console builds for all tools, skips an unavailable remote by default, and removes `dist` and generated `.spec` files when finished. To build a single tool yourself, including a console-free `.pyw` executable:

```powershell
uv run pyinstaller --onefile file_operations.py
uv run pyinstaller --onefile --noconsole connect_smb.pyw
```

Manual builds are written to `dist`. Place `config.yaml` beside each deployed executable.

## 🧩 Project structure

```text
config_ops/
├── *.py / *.pyw          # Executable automation tools
├── deploy.py             # Local and remote executable deployment
├── config.yaml           # Local runtime configuration (created on demand)
├── tests/                # Regression tests
└── winutils_python/      # Shared helper-package submodule
```

The project is currently alpha software. Review paths and use backups before running destructive `mirror`, `move`, archive, or in-place timestamp operations.

## 📄 License

Licensed under the terms in `LICENSE`.
