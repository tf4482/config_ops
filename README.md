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

- [`file_operations.pyw`](file_operations.pyw) runs configured file operation sets.
- [`winutils_python`](winutils_python/README.md) provides the reusable Windows helper modules.

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

## ⚙️ Configuration

If `config.yaml` does not contain a `file_operations` section yet, the script adds an example section automatically.

Example:

```yaml
smb:
  user: 'DOMAIN\user'
  mappings:
    - drive: 'R:'
      share: '\\server\share'

file_operations:
  backup:
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

Before file operations run, configured SMB shares are connected with `net use`.

The SMB password is requested when needed and stored in the config in an obfuscated form for later runs.

## 🚧 Status

This project is usable for first Robocopy-based file operation workflows, but it is still early-stage. Configuration formats and helper APIs may still change.
