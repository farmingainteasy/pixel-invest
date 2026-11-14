#!/bin/zsh
# Paper trader. Double-click in Finder to update now and open the dashboard.
# Normally you never need this: GitHub runs it automatically every evening.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up Python packages..."
  /usr/bin/python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || exit 1
fi
.venv/bin/python -W ignore paper_trader.py "$@"
