#!/usr/bin/env sh
cd "$(dirname "$0")" || exit 1
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-$PWD/.cache/pycache}"
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python kde_main.py "$@"
fi
exec python3 kde_main.py "$@"
