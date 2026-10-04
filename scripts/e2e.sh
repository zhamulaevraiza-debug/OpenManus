#!/usr/bin/env bash
# End-to-end tests of the web product: fake LLM + web server + built UI + Playwright
# (desktop and mobile). All arguments are passed to scripts/e2e.py, e.g.
#   scripts/e2e.sh                         # run the whole suite
#   scripts/e2e.sh -- --project=mobile     # Playwright arguments after "--"
#   scripts/e2e.sh --screenshots           # regenerate docs/screenshots
#
# The Python interpreter is $PYTHON, else the active virtualenv, else python3.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "${PYTHON:-}" ]]; then
  python_bin="$PYTHON"
elif [[ -n "${VIRTUAL_ENV:-}" ]]; then
  python_bin="$VIRTUAL_ENV/bin/python"
else
  python_bin="$(command -v python3)"
fi

if [[ ! -d "$ROOT/web/node_modules" ]]; then
  (cd "$ROOT/web" && npm ci)
fi

exec "$python_bin" "$ROOT/scripts/e2e.py" "$@"
