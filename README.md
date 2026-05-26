# ⚙️ config_ops

> 🪄 Configuration-driven Windows automation scripts for recurring local, network, and file-management tasks.

The project is intentionally script-oriented: each root script owns its own `config.yaml` section, adds an example section when missing, and delegates reusable Windows-specific helpers to the `winutils_python` submodule.

## 🧰 Current tools

- 💾 `file_operations.py` runs named Robocopy operation sets for mirror, copy, and move jobs.
- 🖼️ `archive_media.py` moves configured media files into dated `YYYY/MM/DD` archive folders.
- 🕒 `adjust_file_creation_date.py` parses timestamps from filenames and applies them as Windows file times.
- 🔌 `connect_smb.pyw` connects configured SMB network shares without opening a terminal window.
- 🚀 `ssh_tasks.py` runs named SSH command sets.
- 💡 `peripherals.py` toggles simple URL-controlled peripherals and stores state in the Windows registry.
- 📦 `deploy.py` compiles root `.py` and `.pyw` scripts with PyInstaller and deploys executables from `dist`.
- 🧩 `winutils_python/` contains reusable helper modules for config handling, SMB, Robocopy, menus, and terminal visuals.

## ✅ Requirements

- 🪟 Windows
- 🐍 Python `>=3.13`
- ⚡ `uv`
- 🔐 OpenSSH tools when using `ssh_tasks.py` or remote deployment in `deploy.py`
- 💾 `robocopy` for file operations
- 🔌 `net use` for SMB mappings
- 🌐 `curl.exe` for `peripherals.py`

## 🚀 Setup

After cloning, initialize the submodule and sync dependencies:

```powershell
git submodule update --init --recursive
uv sync
```

This is an application-style `uv` project. The local `winutils_python` submodule is declared as an editable dependency in `pyproject.toml`.

## 🧭 Configuration model

All root scripts read `config.yaml` next to the script or compiled executable. When a section is missing, the script appends a default example section and exits so the file can be configured first.

Named-set tools accept a set name as the first argument. If no argument is provided, they use the shared terminal menu from `winutils_python/menu.py`.

Named-set tools:

- 💾 `file_operations.py`
- 🖼️ `archive_media.py`
- 🕒 `adjust_file_creation_date.py`
- 🚀 `ssh_tasks.py`

Example:

```powershell
uv run python file_operations.py backup
uv run python archive_media.py phone_photos
uv run python adjust_file_creation_date.py screenshots
uv run python ssh_tasks.py manual_backup
```

Without an argument:

```powershell
uv run python file_operations.py
```

The script prints available sets and asks for a number or name.

## 📝 Example `config.yaml`

```yaml
smb:
  user: 'DOMAIN\user'
  mappings:
    - drive: 'R:'
      share: '\\server\share'

file_operations:
  backup:
    smb: true
    robocopy:
      common_options: ['/MT:32', '/W:2', '/R:10', '/XJD', '/XJF', '/XJ', '/XC', '/ETA', '/TEE']
    mirror:
      - source: 'C:\path\to\source'
        target: 'R:\path\to\target'
    copy:
      overwrite: false
      options: ['/E', '/MT:16', '/W:2', '/R:5', '/XJD', '/XJF', '/XJ', '/XC', '/ETA', '/TEE']
      tasks:
        - source: 'C:\path\to\source'
          target: 'E:\path\to\target'
    move:
      overwrite: true
      tasks:
        - source: 'C:\path\to\source'
          target: 'R:\path\to\target'

archive_media:
  phone_photos:
    smb: true
    extensions:
      - .jpg
      - .jpeg
      - .png
    tasks:
      - source: 'R:\pictures\mobilecam'
        target: 'R:\pictures\mobilecam archive'

adjust_file_creation_date:
  screenshots:
    smb: true
    source_folder: 'R:\pictures\screenshots'
    target_folder: 'R:\pictures\screenshots adjusted'
    extensions:
      - .jpg
      - .png
    change_files_in_place: false
    overwrite: false
    hour_adjustment: 0
    patterns:
      - pattern: '^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})'

ssh:
  manual_backup:
    user: user
    host: 192.168.1.12
    port: 51215
    timeout: 300
    command: manual_backup.bash

peripherals:
  registry_path: 'Software\peripherals'
  led:
    on: 'https://example.invalid/led/on'
    off: 'https://example.invalid/led/off'
```

## 🔌 SMB connections

Top-level `smb` defines credentials and mappings. Scripts that support SMB opt in per selected set with `smb: true`.

Per set, `smb` can be:

- 🚫 `false` or omitted to skip SMB connections
- ✅ `true` to use the top-level `smb` configuration

When SMB is enabled and no encrypted password is stored, the password is prompted and then persisted in `config.yaml` as an obfuscated value. Legacy `password` and `password_file` values are removed after storing the encrypted password.

`connect_smb.pyw` directly connects the top-level SMB mappings. Its subprocess calls use Windows no-console creation flags in `winutils_python/connect_smb.py`, so running the `.pyw` script does not open terminal windows for `net use`.

## 💾 File operations

`file_operations.py` runs one named set from `file_operations`.

Supported operation groups:

- 🪞 `mirror`
- 📋 `copy`
- 📦 `move`

Each operation group can be either a list of tasks or a table with `tasks`, `overwrite`, and `options`.

Robocopy exit codes below `8` are treated as successful outcomes. Exit codes `8` and above are collected, summarized, and raised after all configured operations have run.

## 🖼️ Media archive

`archive_media.py` runs one named set from `archive_media`.

For each task:

- 📂 `source` must exist and must be a directory.
- 🎯 `target` is created as needed.
- 🔎 Matching files are moved recursively.
- 📅 File creation time determines the destination folder.
- ♻️ Existing destination files or folders with the same name are removed before moving.
- 🧾 Task failures are collected while later tasks continue.

## 🕒 File creation date adjustment

`adjust_file_creation_date.py` runs one named set from `adjust_file_creation_date`.

Behavior:

- 📎 `extensions` and `patterns` must be non-empty lists.
- 🧩 Regex patterns must define `year` or `year2`, plus `month` and `day`.
- ⏱️ Optional groups are `hour`, `minute`, and `second`.
- 🔁 `hour_adjustment` shifts parsed timestamps.
- ✍️ `change_files_in_place: true` updates matching source files directly.
- 📋 `change_files_in_place: false` copies matching files to `target_folder` or to a `changed_date` folder below the source folder.
- 🛡️ `overwrite: false` creates collision-safe suffixed names like `image_1.jpg`.
- 🧾 Individual file failures are collected while later files continue.

## 🚀 SSH tasks

`ssh_tasks.py` runs one named set from `ssh`.

Required values:

- 👤 `user`
- 🖥️ `host`
- 🔢 `port`
- ⌨️ `command`

Optional value:

- ⏳ `timeout`

The port must be in the range `1..65535`. Timeout must be a positive number when configured. Missing `ssh.exe`, timeouts, and non-zero SSH exit codes are reported clearly.

## 💡 Peripherals

`peripherals.py` controls configured devices by calling their `on` and `off` URLs with `curl.exe`.

Usage examples:

```powershell
uv run python peripherals.py
uv run python peripherals.py led on
uv run python peripherals.py tv toggle
uv run python peripherals.py suspend
```

Supported commands:

- ✅ `on`
- 🚫 `off`
- 🔁 `toggle`
- 💤 `suspend`
- ⏯️ `resume`

If no device is selected, the command is applied to all configured devices. If no command is selected, `toggle` is used. Device state is stored under `peripherals.registry_path` in `HKEY_CURRENT_USER`.

## 📦 Deployment

`deploy.py` finds root `.py` and `.pyw` scripts, excluding itself, compiles them with PyInstaller, and deploys the executables from `dist`.

Deployment targets:

- 🏠 local `%USERPROFILE%\bin`
- 🌐 remote `%USERPROFILE%\bin` over `scp`

The script no longer uses a separate `release` directory. `dist` is cleaned after deployment, and generated `.spec` files are removed.

## 🧩 Helper modules

The `winutils_python` submodule provides reusable helpers:

- 📝 `config.py` for YAML parsing, dumping, scalar parsing, and table validation.
- 🔌 `connect_smb.py` for password obfuscation and SMB `net use` mapping logic.
- 💾 `file_ops.py` for Robocopy command construction and operation-set execution.
- 📋 `menu.py` for shared terminal selection menus.
- 🎨 `visual.py` for terminal-aware status output.

Root scripts still own project-specific `config.yaml` loading, default-section creation, and persistence decisions.

## 🚧 Status

The project is usable for Windows automation workflows but remains script-first and configuration-format changes are still possible.
