# ⚙️ config_ops

> Tiny, configuration-driven Windows system tools.

**config_ops** is an early-stage project for automating recurring Windows tasks through simple configuration files.

## 🌱 Vision

In the future, Config Ops may help with:

- 💾 Backups and file operations
- 🔌 Network share connections
- 🧹 Local maintenance tasks
- 🚀 Small command automations

## 🧩 Current tools

- [`archive_media.pyw`](archive_media.pyw) moves media files into dated archive folders.
- [`connect_smb.pyw`](connect_smb.pyw) connects configured SMB network shares.
- [`file_operations.pyw`](file_operations.pyw) runs configured file operation sets.
- [`ssh_tasks.pyw`](ssh_tasks.pyw) runs configured SSH command sets.
- [`config_support.py`](config_support.py) owns all reads and writes for the project `config.yaml`.
- [`winutils_python`](winutils_python/README.md) provides the reusable Windows helper modules.

`winutils_python` is intentionally helper-only: it does not load, create, or mutate this project’s `config.yaml` directly. All project configuration interaction stays in the root scripts.

## 📦 Project packaging

`config_ops` is an application-style `uv` project. The reusable Windows helper package is kept as the Git submodule `winutils_python` and is declared as an editable local dependency in `pyproject.toml`.

After cloning, initialize the submodule and sync dependencies:

```powershell
git submodule update --init --recursive
uv sync
```

## 💾 File operations

`file_operations.pyw` can run named operation sets from `config.yaml`.

Supported operation groups:

- `mirror` for Robocopy mirror jobs
- `copy` for recursive copy jobs
- `move` for recursive move jobs

The script can be started with a set name:

```powershell
uv run python file_operations.pyw backup
```

Without an argument, it opens either a terminal selection or a small window to choose the operation set.

Robocopy results are collected across all configured jobs. Exit codes below `8` are treated as successful Robocopy outcomes; exit codes `8` and above are summarized and reported as a process error after all jobs have been attempted.

## 🖼️ Media archive

`archive_media.pyw` moves configured media files into dated archive folders based on filesystem creation time:

```powershell
uv run python archive_media.pyw
```

The `archive_media.smb` value controls whether top-level SMB mappings are connected before archiving.

Archive behavior:

- `archive_media.extensions` must be a non-empty list.
- Each configured task source must exist and must be a directory.
- Files are moved into `target/YYYY/MM/DD/filename` folders.
- If one task fails, later tasks still run.
- After all tasks finish, failures are summarized and reported as a process error.

## 🚀 SSH tasks

`ssh_tasks.pyw` runs named SSH command sets from `config.yaml`:

```powershell
uv run python ssh_tasks.pyw manual_backup
```

Without an argument, it opens either a terminal selection or a small window to choose the SSH set.

SSH task behavior:

- `user`, `host`, `port` and `command` are required.
- `port` must be in the range `1..65535`.
- `timeout` is optional and must be a positive number when configured.
- SSH failures, missing `ssh.exe`, and timeouts are reported clearly.
- In non-terminal `.pyw` mode, SSH failure details are shown in a GUI error dialog.

## 🔌 SMB connections

`connect_smb.pyw` connects the top-level `smb` mappings from `config.yaml` without running any file operations:

```powershell
uv run python connect_smb.pyw
```

If `config.yaml` does not contain an `smb` section yet, the script adds an example section and exits so it can be configured first.

## ⚙️ Configuration

The root scripts own the project configuration file. `config_support.py` loads `config.yaml`, appends missing example sections, and persists prompted SMB passwords. Helper modules in `winutils_python` only receive already-loaded configuration data.

If `config.yaml` does not contain a `file_operations` section yet, `file_operations.pyw` adds an example section automatically.

Example:

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
  smb: true
  extensions:
    - .jpg
    - .jpeg
    - .png
  tasks:
    - source: 'R:\pictures\mobilecam'
      target: 'R:\pictures\mobilecam archive'

ssh:
  manual_backup:
    user: user
    host: 192.168.1.12
    port: 51215
    timeout: 300
    command: manual_backup.bash
```

## 🔌 SMB shares

Before file operations run, SMB shares are connected with `net use` only when the selected operation set enables SMB.

Per operation set, `smb` can be:

- `false` or omitted to skip SMB connections
- `true` to use the top-level `smb` configuration

SMB credentials and mappings are always defined in the top-level `smb` section. Operation sets and scripts such as `archive_media` only opt in or out with a boolean `smb` value.

The SMB password is requested when needed and stored in the config in an obfuscated form for later runs.

If an SMB mapping fails, the failure is reported as a process error before file operations continue.

## 🚧 Status

This project is usable for first Robocopy-based file operation workflows, but it is still early-stage. Configuration formats and helper APIs may still change.
