# ⚙️ config_ops

Configuration-driven Windows automation in a single binary: SMB shares, Robocopy sets, media archiving, file dates, SSH commands, and URL-controlled devices. Every task is a named set in one strictly validated `config.yml`, so recurring jobs run the same way every time.

## ✨ Features

- 🔌 **SMB**: Connects shares as drive mappings or direct UNC sessions with `net use`.
- 📦 **Files**: Runs Robocopy `mirror`, `copy`, `move`, and bidirectional `sync` tasks per set.
- 🗃️ **Archive**: Moves media recursively into `YYYY/MM/DD` folders by creation date.
- 🕒 **Dates**: Sets Windows file times from filenames, folder names, or EXIF/QuickTime metadata, or writes metadata from file times.
- 🔐 **SSH**: Runs a named command over `ssh` without ever prompting, with a timeout.
- 💡 **Peripherals**: Triggers device URLs (`on`, `off`, `toggle`, `suspend`, `resume`) and keeps their state in the registry.
- 🛡️ **Strict config**: Unknown keys, wrong types, and `CHANGE_ME` placeholders are rejected with a dotted path.
- 🎯 **Menu**: Starting without arguments (e.g. by double-click) shows an arrow-key menu of all tasks; `files`, `archive`, `dates`, and `ssh` offer a set menu when no set is given.
- 🎨 **Clear output**: One colored emoji line per event; exit codes `0`, `1`, and `130`.

## 📦 Install

Requires Windows, [uv](https://docs.astral.sh/uv/), and the system tools `robocopy`, `net`, `curl.exe`, and OpenSSH `ssh` (plus `scp` to deploy remotely).

```powershell
git clone <repository-url> config_ops
cd config_ops
uv sync
```

## 🧭 Tools

- **Sets**: `files`, `archive`, `dates`, and `ssh` take the name of a configured set. Sets with `smb: true` connect all shares first.
- **Files**: `mirror` can delete extra target content. `sync` keeps the newer file, never purges, and rejects identical or nested folders. UNC paths need a matching `smb.mappings` share. Robocopy exit codes below `8` count as success.
- **Archive**: Replaces an existing file at the destination; the target must not be inside the source.
- **Dates**: `file` reads the filename (source folder only), `folder` the containing folder name, `metadata` the embedded EXIF/QuickTime time, and `metadata_reverse` writes the modification time into the metadata. All but `file` scan recursively.
- **Peripherals**: Words select devices and one command, e.g. `led off`; `toggle` is the default.

## ⚙️ Configuration

`config.yml` is read from the first existing location, without merging: beside the executable, then `~/.config/config_ops/config.yml`. On the first run the template is created at the second location and the app exits with `1`. Edit it, replace every `CHANGE_ME`, and rerun. Quote `"on"` and `"off"` keys, since YAML reads them as booleans.

| Section | Keys |
| --- | --- |
| `smb` | `user`, `password`, `mappings` (`share`, optional `drive`) |
| `files.<set>` | `smb`, `common_options`, `tasks` (`type`, `source`, `target`, `overwrite`, `options`) |
| `archive.<set>` | `smb`, `extensions`, `tasks` (`source`, `target`) |
| `dates.<set>` | `smb`, `mode`, `source_folder`, `extensions`, `patterns`, `target_folder`, `change_files_in_place`, `overwrite`, `hour_adjustment` |
| `ssh.<set>` | `user`, `host`, `port`, `timeout_seconds`, `command` |
| `peripherals` | `registry_path`, `devices.<name>` (`on`, `off`) |

All sections are optional; see [config.example.yml](config.example.yml) for a complete file.

## 🚀 Usage

```powershell
config_ops                          # task menu; a list of sets when output is redirected
config_ops smb                      # connect all shares
config_ops files                    # pick a set from a menu
config_ops files backup             # run the Robocopy tasks of a set
config_ops archive photos           # archive media by creation date
config_ops dates screenshots        # set file dates from names or metadata
config_ops ssh remote_backup        # run an SSH command
config_ops peripherals led off      # switch one device off
config_ops peripherals              # toggle all devices
config_ops --help
```

| Exit code | Meaning |
| --- | --- |
| `0` | Everything succeeded |
| `1` | Configuration, validation, dependency, or runtime error |
| `130` | Interrupted by the user |

## ⏱️ Deploy

Build the single binary and copy `dist/config_ops.exe` to a folder on `PATH`, for example `~/bin`:

```powershell
uv run pyinstaller config_ops.spec
```

Put `config.yml` next to the executable or in `~/.config/config_ops/`. Schedule runs with the Windows Task Scheduler, for example `config_ops.exe files backup` daily; the app runs once and exits.

## 🧪 Develop

```powershell
uv sync
uv run ruff check
uv run ruff format --check
uv run pyright
uv run pytest
```

`main.py` starts the app from source: `uv run main.py files backup`.

## 📜 License

[MIT](LICENSE)
