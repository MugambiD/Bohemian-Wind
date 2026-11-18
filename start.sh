#!/bin/sh
# Start Bohemian Wind on macOS or Linux. The first start sets up a private copy of the Python packages it needs.
set -e
cd "$(dirname "$0")"
VENV="${BOHEMIAN_WIND_VENV:-$HOME/.bohemian-wind/venv}"
if [ ! -x "$VENV/bin/python" ] || ! cmp -s requirements.txt "$VENV/requirements.txt"; then
  echo "Setting up Bohemian Wind. This happens once and takes a few minutes."
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install --upgrade pip --quiet
  "$VENV/bin/python" -m pip install -r requirements.txt
  cp requirements.txt "$VENV/requirements.txt"
fi
exec "$VENV/bin/python" main.py "$@"
