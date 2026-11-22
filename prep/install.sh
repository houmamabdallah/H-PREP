#!/bin/bash
set -eu
python3 - "$(dirname "$0")" <<'PY'
import sys
from pathlib import Path

source = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(source))
from hprep_config import config_file, setting_path

destination = setting_path("scripts_dir")
destination.mkdir(parents=True, exist_ok=True)
# Resolve's menu runner may have neither a useful working directory nor __file__.
header = (
    "import os, sys\n"
    f"sys.path.insert(0, {str(source)!r})\n"
    f"os.environ.setdefault('HPREP_CONFIG', {str(config_file())!r})\n"
)
for script in sorted((source / "resolve_scripts").glob("*.py")):
    target = destination / script.name
    target.write_text(header + script.read_text(encoding="utf-8"), encoding="utf-8")
    print(target)
PY
