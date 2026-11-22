"""Read H-PREP settings from the selected JSON file."""
import json
import os
from pathlib import Path


def config_file():
    default = Path(__file__).resolve().parents[1] / "config.json"
    return Path(os.environ.get("HPREP_CONFIG") or default).expanduser().resolve()


def cfg(key):
    path = config_file()
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SystemExit(f"Cannot read {key} from {path}: {error}") from None
    if not isinstance(values, dict) or key not in values or values[key] in (None, ""):
        raise SystemExit(f"Missing config key '{key}' in {path}")
    return values[key]


def setting_path(key):
    value = cfg(key)
    if not isinstance(value, str):
        raise SystemExit(f"Config key '{key}' must be a path")
    path = Path(value).expanduser()
    return path if path.is_absolute() else config_file().parent / path


if __name__ == "__main__":
    import sys
    print(setting_path(sys.argv[1]))
