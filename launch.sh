#!/usr/bin/env bash
# Launches Rýchlik using the project's own venv, regardless of caller's cwd.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
exec ./.venv/bin/python main.py
