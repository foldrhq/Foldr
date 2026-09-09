# FOLDR — Complete Functionality Guide

**Author:**  Qasim Sethar | **GitHub:** github.com/foldrhq/Foldr | **Version:** 2.1

---

## Table of Contents

1. [Install](#install)
2. [Quick Start](#quick-start)
3. [All Commands Reference](#all-commands-reference)
4. [Watch Mode](#watch-mode)
5. [Ignore Rules](#ignore-rules)
6. [Custom Categories (config.toml)](#custom-categories)
7. [Undo & History](#undo--history)
8. [Duplicate Removal](#duplicate-removal)
9. [Per-OS File Locations](#per-os-file-locations)
10. [Default Categories](#default-categories)
11. [Troubleshooting](#troubleshooting)

---

## Install

```bash
pip install foldr
```

One command. Everything included. No extras, no choices. Requires Python 3.10+.

Works identically on **Linux**, **macOS**, and **Windows**.

---

## Quick Start

```bash
foldr ~/Downloads --preview          # see what would happen
foldr ~/Downloads                    # organize (preview → confirm → move)
foldr watch ~/Downloads              # organize now + keep watching forever
foldr undo                           # restore the last operation
```

**Windows paths with spaces:**
```powershell
foldr "D:\My Downloads" --preview
foldr "D:\My Downloads"
```

---

## All Commands Reference

### Organize a directory

```bash
foldr <path>                          # show preview, ask to confirm, then move
foldr <path> --preview                # dry-run: show plan, nothing moves
foldr <path> --recursive              # include subdirectories
foldr <path> --recursive --depth 2    # limit recursion to 2 levels
foldr <path> --follow-links           # follow symbolic links
foldr <path> --ignore "*.log" "tmp/"  # skip these patterns this run
foldr <path> --no-ignore              # disable ALL ignore rules
foldr <path> --show-ignored           # show which files were skipped
foldr <path> --config myconfig.toml   # use a custom category config
foldr <path> --verbose                # print every file as it moves
foldr <path> --quiet                  # suppress all output
```

### Watch mode

```bash
foldr watch <path>                    # organize now + keep watching
foldr watch <path> --recursive        # watch subdirectories too
foldr watch <path> --preview          # watch mode but log only, no moves
foldr watches                         # list all active watchers
foldr unwatch <path>                  # stop a watcher
foldr unwatch                         # interactive picker if no path given
```

### Undo & history

```bash
foldr undo                            # undo the most recent operation
foldr undo --id a1b2c3                # undo a specific operation by ID
foldr undo --preview                  # show what would be restored
foldr history                         # list last 50 operations
foldr history --all                   # list all operations
```

### Dedup (duplicate removal)

```bash
# ⚠ IRREVERSIBLE — always preview first
foldr <path> --dedup keep-newest --preview
foldr <path> --dedup keep-newest      # keep newest, delete older
foldr <path> --dedup keep-largest     # keep biggest file, delete smaller
foldr <path> --dedup keep-oldest      # keep oldest, delete newer
```

### Config

```bash
foldr config                          # show all config file paths
foldr config --edit                   # open config.toml in your editor
foldr config --edit --ignore-file     # open .foldrignore in your editor
```

---

## Watch Mode

### What it does

```bash
foldr watch ~/Downloads
```

1. **Immediate organize:** FOLDR runs a full organize pass on the directory right now. Every existing file gets moved to its category folder.
2. **Continuous watching:** FOLDR stays running in the background. Any file you drop, copy, or move into the folder gets organized within ~1 second.
3. **Re-organizes on re-drop:** If you move an already-organized file back to the root, it gets organized again. No stale state.

### Check status

```bash
foldr watches
```

Output shows: directory, start time, mode (live/preview), recursive setting, files organized, PID.

### Stop watching

```bash
foldr unwatch ~/Downloads    # by path
foldr unwatch                # interactive list
```

### Watch logs

Every file move and error is written to:

```
~/.foldr/watch_logs/<dirname>.log       # Linux / macOS
%USERPROFILE%\.foldr\watch_logs\<dirname>.log  # Windows
```

If watch mode says "started" but nothing moves, check this log file first.

### How the background daemon works

`foldr watch` spawns a background subprocess (`_watch-daemon`) using the absolute path to your Python interpreter. The daemon:

- Runs independently of your terminal session
- Survives terminal close
- Stops when you run `foldr unwatch` or shut down the machine
- Does **not** restart automatically after reboot (by design — use `foldr watch` again)

### Watch mode on Linux

FOLDR uses **inotify** on Linux (kernel-native, 0% CPU when idle). If you hit the inotify watch limit:

```bash
echo fs.inotify.max_user_watches=524288 | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

### Watch mode on macOS

Uses **kqueue** / **FSEvents** (0% CPU when idle). No configuration needed.

### Watch mode on Windows

Uses **ReadDirectoryChangesW** (0% CPU when idle). No configuration needed. The daemon runs as a windowless background process (uses `pythonw.exe`) — no console window appears.

---

## Ignore Rules

### Layer 1: Local `.foldrignore` (per-directory)

Create a `.foldrignore` file inside the directory you're organizing:

```
# .foldrignore inside ~/Downloads
*.log
*.tmp
tmp/
build/
DO_NOT_MOVE*
```

### Layer 2: Global ignore (`~/.foldr/.foldrignore`)

Applies to every `foldr` run everywhere. Edit it with:

```bash
foldr config --edit --ignore-file
```

Example:
```
# ~/.foldr/.foldrignore
*.tmp
*.bak
desktop.ini
~$*
Thumbs.db
.DS_Store
```

### Layer 3: CLI `--ignore` (one run only)

```bash
foldr ~/Downloads --ignore "*.log" "temp/" "DRAFT_*"
```

### Disabling ignores

```bash
foldr ~/Downloads --no-ignore       # disable all rules for this run
foldr ~/Downloads --show-ignored    # see what was skipped and why
```

### Pattern syntax

```
*.log         any file ending in .log
*.bak         any file ending in .bak
Thumbs.db     exact filename
~$*           any file starting with ~$
tmp/          directory named tmp
build/        directory named build
DRAFT_*       any file starting with DRAFT_
```

---

## Custom Categories

FOLDR creates `~/.foldr/config.toml` automatically on first run. Edit it to customize.

### Add extensions to an existing category

```toml
[Images]
extensions = [".heic", ".avif", ".raw"]
```

### Rename the folder a category uses

```toml
[Code]
folder = "Source_Code"
```

### Create a brand new category

```toml
[RAW Photos]
folder     = "RAW_Photos"
extensions = [".cr2", ".nef", ".arw", ".dng", ".orf"]

[Design]
folder     = "Design"
extensions = [".fig", ".sketch", ".xd", ".psd", ".ai"]

[Datasets]
folder     = "Datasets"
extensions = [".parquet", ".feather", ".h5", ".npy", ".npz"]
```

### Replace built-ins entirely

```toml
[foldr]
merge = false       # your config replaces built-ins, not extends them

[My Documents]
folder     = "Documents"
extensions = [".pdf", ".doc", ".docx", ".txt"]
```

### Test your config

```bash
foldr ~/Downloads --preview --config ~/.foldr/config.toml
```

---

## Undo & History

Every `foldr` organize operation writes a JSON record to `~/.foldr/history/`.

### How undo works

```bash
foldr history             # see all operations
foldr undo                # undo the most recent
foldr undo --id a1b2      # undo a specific one (get ID from history)
foldr undo --preview      # see what would be restored without moving
```

Operations are undone independently — undoing operation #1 does not require undoing operation #2 first. If a file was moved again after the operation you're undoing, FOLDR skips it and tells you clearly.

### What cannot be undone

`--dedup` permanently deletes files. The deletion is recorded in history so you can see what happened, but the files are gone. **Always `--preview` before deduping.**

---

## Duplicate Removal

FOLDR finds files with identical content (SHA-256 hash) and removes copies.

```bash
# Step 1: preview (always do this first)
foldr ~/Downloads --dedup keep-newest --preview

# Step 2: execute
foldr ~/Downloads --dedup keep-newest
```

**Strategies:**

| Strategy | Keeps | Deletes |
|----------|-------|---------|
| `keep-newest` | Most recently modified | Older copies |
| `keep-largest` | Biggest file | Smaller copies |
| `keep-oldest` | Earliest modified | Newer copies |

---

## Per-OS File Locations

### Linux / macOS

| Purpose | Path |
|---------|------|
| Config directory | `~/.foldr/` |
| Category config | `~/.foldr/config.toml` |
| Global ignore | `~/.foldr/.foldrignore` |
| History | `~/.foldr/history/` |
| Watch logs | `~/.foldr/watch_logs/` |
| Active watchers | `~/.foldr/watches.json` |

### Windows

| Purpose | Path |
|---------|------|
| Config directory | `C:\Users\<you>\.foldr\` |
| Category config | `C:\Users\<you>\.foldr\config.toml` |
| Global ignore | `C:\Users\<you>\.foldr\.foldrignore` |
| History | `C:\Users\<you>\.foldr\history\` |
| Watch logs | `C:\Users\<you>\.foldr\watch_logs\` |
| Active watchers | `C:\Users\<you>\.foldr\watches.json` |

---

## Default Categories

| Category | Folder | Extensions (sample) |
|----------|--------|---------------------|
| Documents | `Documents/` | `.pdf` `.doc` `.docx` `.odt` `.rtf` `.tex` `.md` `.pages` |
| Images | `Images/` | `.jpg` `.jpeg` `.png` `.gif` `.bmp` `.tiff` `.webp` `.heic` |
| Videos | `Videos/` | `.mp4` `.mkv` `.mov` `.avi` `.wmv` `.flv` `.webm` |
| Audio | `Audio/` | `.mp3` `.wav` `.aac` `.flac` `.ogg` `.m4a` `.wma` |
| Archives | `Archives/` | `.zip` `.rar` `.7z` `.tar` `.gz` `.bz2` `.xz` |
| Code | `Code/` | `.py` `.js` `.ts` `.html` `.css` `.java` `.cpp` `.c` `.rs` `.go` |
| Scripts | `Scripts/` | `.sh` `.bash` `.zsh` `.fish` `.ps1` `.bat` `.cmd` |
| Spreadsheets | `Spreadsheets/` | `.xlsx` `.xls` `.csv` `.ods` |
| Presentations | `Presentations/` | `.pptx` `.ppt` `.odp` |
| Databases | `Databases/` | `.db` `.sqlite` `.sqlite3` |
| Executables | `Executables/` | `.exe` `.msi` `.deb` `.rpm` `.apk` `.dmg` |
| Fonts | `Fonts/` | `.ttf` `.otf` `.woff` `.woff2` |
| Ebooks | `Ebooks/` | `.epub` `.mobi` `.azw` |
| Notebooks | `Notebooks/` | `.ipynb` |
| Text & Data | `Text_Data/` | `.txt` `.json` `.xml` `.yaml` `.toml` `.log` |
| Machine Learning | `Machine_Learning/` | `.pkl` `.h5` `.onnx` `.pt` `.pth` |

Files with unrecognised extensions are **never moved**.

---

## Troubleshooting

### Watch mode says "started" but nothing moves

Check the log file:
```bash
cat ~/.foldr/watch_logs/Downloads.log    # Linux / macOS
type %USERPROFILE%\.foldr\watch_logs\Downloads.log   # Windows
```

The log will show either the error that crashed the processor thread or confirmation that the observer is running and receiving events.

### "Not a valid directory" on Windows

Paths with spaces must be quoted:
```powershell
foldr "D:\My Downloads"
foldr watch "D:\My Downloads"
```

### config.toml syntax error on startup

Open and fix it:
```bash
foldr config --edit
```

Or delete it and let FOLDR regenerate a clean version:
```bash
rm ~/.foldr/config.toml     # Linux / macOS
del %USERPROFILE%\.foldr\config.toml    # Windows
```

### `foldr watches` shows nothing after `foldr watch`

The PID in `watches.json` might be stale from a previous session. Run `foldr watch` again to start a fresh daemon.

### inotify limit on Linux (watch mode fails)

```bash
echo fs.inotify.max_user_watches=524288 | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

### `foldr undo` says file "not found at destination"

The file was moved again after the operation you're trying to undo. Run `foldr history` to find the more recent operation and undo that one first, or just re-organize.