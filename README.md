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

- [`connect_smb.pyw`](connect_smb.pyw) connects configured SMB network shares.
- [`file_operations.pyw`](file_operations.pyw) runs configured file operation sets.
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
```

## 🔌 SMB shares

Before file operations run, SMB shares are connected with `net use` only when the selected operation set enables SMB.

Per operation set, `smb` can be:

- `false` or omitted to skip SMB connections
- `true` to use the top-level `smb` configuration
- a table to define set-specific SMB credentials and mappings

The SMB password is requested when needed and stored in the config in an obfuscated form for later runs.

If an SMB mapping fails, the failure is reported as a process error before file operations continue.

## 🚧 Status

This project is usable for first Robocopy-based file operation workflows, but it is still early-stage. Configuration formats and helper APIs may still change.
