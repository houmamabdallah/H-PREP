#!/bin/bash
# Copy project backups, renders and prep files to the archive.
set -eu
config="$(dirname "$0")/../prep/hprep_config.py"
path_setting() { python3 "$config" "$1"; }
dest=$(path_setting backup_destination)
backups=$(path_setting project_backups)
renders=$(path_setting renders_dir)
prep=$(path_setting prep_dir)
mkdir -p "$backups"
exec >> "$backups/weekly_backup.log" 2>&1
echo "Backup started: $(date)"
if [ ! -d "$dest" ]; then
  echo "Backup destination is unavailable: $dest"
  exit 1
fi
rsync -av "$backups/" "$dest/DaVinci Resolve Backups/"
mkdir -p "$dest/Renders" "$dest/Prep"
rsync -av "$renders/" "$dest/Renders/"
rsync -av "$prep/" "$dest/Prep/"
echo "Backup finished: $(date)"
