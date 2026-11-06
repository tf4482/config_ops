# ⚙️ config_ops

Configuration-driven Windows automation in a single binary: SMB shares, Robocopy sets, media archiving, file dates, and SSH commands. Every task is a named set in one strictly validated `config.yml`, so recurring jobs run the same way every time.

## ✨ Features

- 🔌 **SMB**: Connects shares as drive mappings or direct UNC sessions with `net use`.
- 📦 **Files**: Runs Robocopy `mirror`, `copy`, `move`, and bidirectional `sync` tasks per set, mirrors single files with `mirror-file`, and zips a folder into the target with `zip`.
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

## 🔄 How it works

1. **Load**: `config.yml` is read and validated completely before anything runs; a typo stops the run with a dotted path such as `files.backup.tasks[0].type`, never halfway through.
2. **Connect**: If the set has `smb: true`, its shares are connected first with `net use` (the global `smb.mappings`, or the set's own `mappings`).
3. **Run**: The tasks of the set run one after another, in the order of the list. A failed task does not stop the others.
4. **Report**: One colored line per task and a closing `📋 N succeeded · N failed` summary; the exit code is `1` if any task failed.

## 🧭 Tools

Every tool takes the name of a configured set (`config_ops <tool> <set>`); without a set, a menu opens. Sets with `smb: true` connect all `smb.mappings` shares first; a set with its own `mappings` list (same format) connects only those instead.

### 🔌 `smb`: connect shares

Maps each share to a drive letter (`drive: "R:"`) or opens a direct UNC session when no drive is given.

```yaml
smb:
  user: 'DOMAIN\me'
  mappings:
    - share: '\\server\main'           # UNC session
    - share: '\\server\backup'
      drive: "R:"                      # drive mapping
```

- **Password**: Read from `smb.password_file` (a bare name is looked up beside the executable, an absolute or `~` path is used as given); default `~/.config/.smbcredentials`. The file holds the plain password, or `username=` / `password=` lines. A `username=` line wins over `smb.user`.
- **Unreachable shares** (server off, name not found, network down): their tasks are skipped, the run still ends with `0`, and a closing `⚠️ Skipped, SMB unreachable` line names the share. Wrong credentials stay an error.

### 📦 `files`: Robocopy and zip tasks

| `type` | What it does |
| --- | --- |
| `mirror` | Makes the target identical to the source and deletes extra target content |
| `copy` | Copies new and changed files, never deletes |
| `move` | Moves files and folders to the target |
| `sync` | Two-way: keeps the newer file on both sides, never purges; rejects identical or nested folders |
| `mirror-file` | Copies one file when the target is missing or differs (size or time), creating folders |
| `zip` | Zips the source folder into `<target>\<folder name>.zip` |

```yaml
files:
  backup:
    smb: true
    tasks:
      - type: mirror
        source: 'C:\data'
        target: 'R:\data'
        exclude_dirs: [".venv", "node_modules"]   # adds to robocopy.exclude_dirs
      - type: copy
        source: 'C:\documents'
        target: 'R:\documents'
        overwrite: false                          # keep existing target files
      - type: mirror-file
        source: 'C:\settings\app.ini'
        target: ['D:\backup\app.ini', 'E:\backup\app.ini']   # one task per target
      - type: zip
        source: 'C:\settings\.ssh'
        target: 'R:\ssh'                         # -> R:\ssh\.ssh.zip
        exclude_files: ["known_hosts"]
```

- **Targets**: `mirror` and `mirror-file` accept a list of targets; each is a task of its own.
- **Robocopy**: Exit codes below `8` count as success. UNC paths need a matching share in the mappings that apply to the set. `options` adds raw Robocopy options to one task.
- **Zip**: Builds the archive locally first, so a failed run never leaves a partial file. `overwrite: false` keeps an existing archive.
- **Excludes**: `robocopy.exclude_dirs` / `exclude_files` apply to every `files` task (including `zip`); task-level lists add to them. Nothing is excluded unless listed; `config.example.yml` shows the usual system folders.
- **Options**: `robocopy.common_options` is the default for sets without their own `common_options`. A list replaces the built-in defaults (`/MT:32 /W:2 /R:10 /XJ /XC /NJH /NFL /NDL /NC /NS /NP`), so keep the `/N*` options to keep the output short.
- **Output**: A progress bar and one colored line per task (copied, unchanged, removed, failed); Robocopy's own output appears only on failures.

### 🗃️ `archive`: sort media by date

Moves files with the listed `extensions` recursively into `YYYY/MM/DD` folders of the target, by creation date. An existing file at the destination is replaced; the target must not be inside the source.

```yaml
archive:
  photos:
    extensions: [".jpg", ".png", ".mp4"]
    tasks:
      - source: 'C:\import'
        target: 'D:\photos'          # -> D:\photos\2026\11\06\IMG_001.jpg
```

### 🕒 `dates`: fix file times

| `mode` | Source of the time |
| --- | --- |
| `file` | Filename (source folder only, not recursive) |
| `folder` | Name of the containing folder (recursive) |
| `metadata` | Embedded EXIF/QuickTime time (recursive) |
| `metadata_reverse` | Writes the modification time into the metadata (recursive) |

`patterns` are regular expressions with the named groups `year` (or two-digit `year2`), `month`, `day`, and optionally `hour`, `minute`, `second`; the first matching pattern wins and missing time parts default to midnight.

```yaml
dates:
  screenshots:
    mode: file
    source_folder: 'C:\screenshots'
    extensions: [".jpg", ".png"]
    patterns:
      - '^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})_(?P<hour>\d{2})(?P<minute>\d{2})'
    hour_adjustment: 1                 # shift by hours, e.g. for time zones
  camera:
    mode: metadata
    source_folder: 'C:\camera'
    extensions: [".jpg", ".mp4"]
    change_files_in_place: false       # write adjusted copies instead
    target_folder: 'C:\camera-fixed'   # default: <source>\changed_date
    overwrite: false                   # keep existing copies
```

`change_files_in_place` defaults to `true`; with `false`, files are copied to `target_folder` with the new time.

### 🔐 `ssh`: run a remote command

Runs one command with `ssh`, never prompts (key-based login required), and stops after `timeout_seconds` (maximum one day).

```yaml
ssh:
  remote_backup:
    user: "backup"
    host: "192.0.2.10"
    port: 22
    timeout_seconds: 300
    command: "ls"
```

### 🎯 Menus

`config_ops` without arguments (e.g. by double-click) shows an arrow-key menu of all tasks; `files`, `archive`, `dates`, and `ssh` show a set menu when no set is given. With redirected output, a plain list of sets is printed instead.

### 🖥️ Example output

```text
✅ \\server\main · connected
📦 mirror · C:\data → R:\data
✅ mirror · 12 copied · 480 unchanged · 3.2s
📦 zip · C:\settings\.ssh → R:\ssh
✅ zip · 7 copied · 0.1s
📋 2 succeeded · 0 failed
```

A failed task is shown in red with its exit code and the relevant Robocopy lines (`📄`); tasks on unreachable shares show as `⏭️ ... skipped`. The exact counters vary by task type.

## ⚙️ Configuration

`config.yml` is read from the first existing location, without merging: `~/.config/config_ops/config.yml`, then beside the executable. On the first run the template is created at the first location and the app exits with `1`. Edit it, replace every `CHANGE_ME`, and rerun. Quote `"on"` and `"off"` keys, since YAML reads them as booleans.

| Section | Keys |
| --- | --- |
| `smb` | optional `user`, optional `password_file`, `mappings` (`share`, optional `drive`) |
| `robocopy` | optional `common_options`, `exclude_dirs`, `exclude_files` for all Robocopy tasks |
| `files.<set>` | `smb`, `mappings`, `common_options`, `tasks` (`type`, `source`, `target`, `overwrite`, `options`, `exclude_dirs`, `exclude_files`) |
| `archive.<set>` | `smb`, `mappings`, `extensions`, `tasks` (`source`, `target`) |
| `dates.<set>` | `smb`, `mappings`, `mode`, `source_folder`, `extensions`, `patterns`, `target_folder`, `change_files_in_place`, `overwrite`, `hour_adjustment` |
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
config_ops ssh                      # pick an SSH set from a menu
config_ops archive                  # pick an archive set from a menu
config_ops --help
```

| Exit code | Meaning |
| --- | --- |
| `0` | Everything succeeded |
| `1` | Configuration, validation, dependency, or runtime error |
| `130` | Interrupted by the user |

## 🍳 Recipes

**Nightly backup to a NAS**: define a `files` set with `smb: true`, then schedule it.

```powershell
schtasks /Create /SC DAILY /ST 02:00 /TN "config_ops backup" /TR "config_ops.exe files backup"
```

**Back up SSH keys**: zip a folder and leave out noisy files.

```yaml
files:
  secrets:
    tasks:
      - type: zip
        source: 'C:\Users\me\.ssh'
        target: 'D:\backup\secrets'
        exclude_files: ["known_hosts", "*.tmp"]
```

**Keep two folders in step**: `sync` keeps the newer file on both sides and never deletes.

```yaml
files:
  documents:
    tasks:
      - type: sync
        source: 'C:\documents'
        target: 'R:\documents'
```

**Import camera files**: fix the dates from the filenames first, then archive.

```powershell
config_ops dates screenshots
config_ops archive photos
```

**A different server per set**: give the set its own `mappings`; the global ones are ignored for it.

```yaml
files:
  archive_nas:
    smb: true
    mappings:
      - share: '\\other-server\archive'
        drive: "A:"
    tasks:
      - type: copy
        source: 'C:\old'
        target: 'A:\old'
```

## 🩺 Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Exit `1` right after the first start | The template was just created; edit it and replace every `CHANGE_ME` |
| Error naming `CHANGE_ME` | A placeholder is still in the config; set a real value or remove the section |
| Unknown key or wrong type | The message names the dotted path; fix that key, the config is strict |
| `⚠️ Skipped, SMB unreachable` | The server is off or the network is down; the run still ends with `0` |
| SMB login fails | Check `smb.user` and the password file; wrong credentials are an error, not a skip |
| A `files` task fails | Read the `📄` lines under it for Robocopy's message |
| Keys `on` / `off` behave oddly | YAML reads them as booleans; quote them: `"on":` |

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
