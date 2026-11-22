# H-PREP

H-PREP groups similar shots for grading, carries grades across a re-cut,
archives gallery stills and backs up local Resolve work.

## Requirements and setup

Use DaVinci Resolve Studio with Python scripting enabled and Python 3.9 or
later. For terminal tools that connect to Resolve, start Resolve and set
**Preferences → System → General → External scripting using** to **Local**.
The weekly scheduler uses macOS launchd. The backup script needs rsync.

1. From the repository root, run `cp config.example.json config.json`.
2. Edit the settings for the tools you use. Paths can be absolute or relative
   to the config file. Set `HPREP_CONFIG` to select a different JSON file.
3. Set `scripts_dir` to Resolve's Fusion Scripts/Utility directory and
   `resolve_modules` to its scripting Modules directory. Run
   `bash prep/install.sh` and reopen Resolve's **Workspace → Scripts** menu.
   Run the installer again if you move this checkout or select a new config.
   When upgrading, remove obsolete menu scripts from the Utility directory.

The example lists every setting. Use your own writable directories for
reports, templates, conform output, gallery work and backups. Supply your own
DCTL files in `looks_dir` under `lut_root`. Put finished clip mattes in
`mattes_dir`. Keep gallery scratch space separate from the archive.

## Tools

| Tool | What it does |
|---|---|
| Prep menu | Preview visual shot groups on a contact sheet, then make a grouped timeline. Save or apply a grade tree, spread a group grade, apply a DCTL, attach existing clip mattes, or report timeline status. |
| Conform | Detect cuts in a flattened send, map shots from a supplied frame correspondence, and apply existing DRX grades to a prepared target timeline. |
| `pg_export.py` | Export one or more projects' gallery albums as JPG and DRX. Record checksums for repeat runs. Requires ffmpeg. |
| `run_all_local_db.sh` | Export each entry in `projects`. An empty list does nothing. Use `["PROJECT_A"]` for one project, or `[{"job":"PROJECT_A","names":["PROJECT_A","PROJECT_A v2"]}]` to archive versions together. |
| `pg_import.py` | Import archived stills into shared albums in a saved project. It records progress in `gallery_tmp`. Set `skip_import_jobs` for albums to omit during automatic discovery. |
| `pg_move.py` | Map local stills to projects, export or sweep one job, verify the archive, and list local DPX files eligible for removal. With `sweep_converter` set to `auto`, it tries oiiotool, sips and ffmpeg. An explicit converter receives input and output paths. |
| `pg_relink.py` | Report stills with missing gallery links. Restore hard links from backups, remove verified duplicate links, or estimate space used. |
| `resolve_busy.py` | Report whether a project is open. Gallery export checks this before switching projects. |
| `resolve_weekly_backup.sh` | Copy the configured project backups, renders and prep directories into the backup destination. Write a log in `project_backups`. |

Shot grouping needs NumPy in Resolve's Python. The interpreter in
`group_python` needs NumPy and Pillow, plus access to the configured ffmpeg
executable. Install PyTorch and Transformers there to use DINOv2 image
features. Conform frame analysis uses NumPy with ffmpeg or OpenCV. See the
[prep guide](prep/README.md) and [conform guide](prep/conform/README.md).

## Backup schedule

Copy `tools/launchd/com.example.resolve.weeklybackup.plist` into your
LaunchAgents directory. In that copy, replace the script and two log paths
with absolute paths and create the log directory first. The script uses the
checkout's config by default. For another file, set `HPREP_CONFIG` in the
plist's `EnvironmentVariables` dictionary. Load the edited copy with
`launchctl bootstrap "gui/$(id -u)" /path/to/com.example.resolve.weeklybackup.plist`.
It runs at 11 pm on Sundays.

## Safety

The weekly backup copies new and changed files and leaves destination-only
files in place. Gallery tools print the paths before removing temporary files
or duplicate links. `pg_move.py --free JOB PROJECT_A` verifies the archived
pictures, lists eligible local files and prints a command for you to review.
Gallery export can switch the current project. Save your work and close the
project before starting a batch.
