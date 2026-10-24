# ⚙️ config_ops

Configuration-driven Windows automation in a single binary: SMB shares, Robocopy sets, media archiving, file dates, and SSH commands. Every task is a named set in one strictly validated `config.yml`, so recurring jobs run the same way every time.

## ✨ Features

- 🔌 **SMB**: Connects shares as drive mappings or direct UNC sessions with `net use`.
- 📦 **Files**: Runs Robocopy `mirror`, `copy`, `move`, and bidirectional `sync` tasks per set, and mirrors single files with `mirror-file`.
- 🗃️ **Archive**: Moves media recursively into `YYYY/MM/DD` folders by creation date.
- 🕒 **Dates**: Sets Windows file times from filenames, folder names, or EXIF/QuickTime metadata, or writes metadata from file times.
- 🔐 **SSH**: Runs a named command over `ssh` without ever prompting, with a timeout.
- 🛡️ **Strict config**: Unknown keys, wrong types, and `CHANGE_ME` placeholders are rejected with a dotted path.
- 📊 **Robocopy view**: A progress bar for the tasks of a set and one colored result line per task (copied, unchanged, removed, failed), with Robocopy's own output only on failures.
- 🎯 **Menu**: Starting without arguments (e.g. by double-click) shows an arrow-key menu of all tasks; `files`, `archive`, `dates`, and `ssh` offer a set menu when no set is given.
- 🎨 **Clear output**: One colored emoji line per event; exit codes `0`, `1`, and `130`.

## 📦 Install

Requires Windows, [uv](https://docs.astral.sh/uv/), and the system tools `robocopy`, `net`, and OpenSSH `ssh` (plus `scp` to deploy remotely).

```powershell
git clone <repository-url> config_ops
cd config_ops
uv sync
```

## 🧭 Tools

- **Sets**: `files`, `archive`, `dates`, and `ssh` take the name of a configured set. Sets with `smb: true` connect all shares first.
- **Files**: `mirror-file` copies one file when the target is missing or differs (size or time), creating target folders; `overwrite: false` keeps an existing target. `mirror` and `mirror-file` accept a list of targets (`target:` with several lines); each target is a task of its own. `mirror` can delete extra target content. `sync` keeps the newer file, never purges, and rejects identical or nested folders. UNC paths need a matching `smb.mappings` share. Robocopy exit codes below `8` count as success.
- **Archive**: Replaces an existing file at the destination; the target must not be inside the source.
- **Dates**: `file` reads the filename (source folder only), `folder` the containing folder name, `metadata` the embedded EXIF/QuickTime time, and `metadata_reverse` writes the modification time into the metadata. All but `file` scan recursively.
- **Unreachable shares**: When a share cannot be reached (server off, name not found, network down), its tasks are skipped, the run still succeeds with exit code `0`, and a closing `⚠️ Skipped, SMB unreachable` line names the share and the number of skipped items. Wrong credentials stay an error.
- **SMB password**: Read from the file named by `smb.password_file`. A bare file name is looked up beside the executable, an absolute or `~` path is used as given. Without the key, `~/.config/.smbcredentials` is used. The file holds the plain password in UTF-8, or a credentials file with `username=` and `password=` lines (other lines are ignored). A `username=` line wins over `smb.user`, so `smb.user` can be left out.

## ⚙️ Configuration

`config.yml` is read from the first existing location, without merging: `~/.config/config_ops/config.yml`, then beside the executable. On the first run the template is created at the first location and the app exits with `1`. Edit it, replace every `CHANGE_ME`, and rerun. Quote `"on"` and `"off"` keys, since YAML reads them as booleans.

| Section | Keys |
| --- | --- |
| `smb` | optional `user`, optional `password_file`, `mappings` (`share`, optional `drive`) |
| `files.<set>` | `smb`, `common_options`, `tasks` (`type`, `source`, `target`, `overwrite`, `options`) |
| `archive.<set>` | `smb`, `extensions`, `tasks` (`source`, `target`) |
| `dates.<set>` | `smb`, `mode`, `source_folder`, `extensions`, `patterns`, `target_folder`, `change_files_in_place`, `overwrite`, `hour_adjustment` |
| `ssh.<set>` | `user`, `host`, `port`, `timeout_seconds`, `command` |

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

Put `config.yml` in `~/.config/config_ops/` or next to the executable. Schedule runs with the Windows Task Scheduler, for example `config_ops.exe files backup` daily; the app runs once and exits.

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
