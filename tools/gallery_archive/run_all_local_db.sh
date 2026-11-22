#!/bin/bash
set -eu
exec python3 "$(dirname "$0")/pg_export.py" --configured
