#!/usr/bin/env bash
# Re-lock the aces-engineering venv. Resolves requirements.in inside the aces-builder image (the same Arch Python the
# package is built with), binary wheels only, then lock.py pins every resolved distribution with the sha256 of its
# compatible PyPI wheels into requirements.txt. Run from Git Bash on Windows (Docker Desktop) or Linux.
#
#   bash src/aces-engineering/lock.sh        then rebuild: bash bin/build aces-engineering
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
hostpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
MSYS_NO_PATHCONV=1 docker run --rm -v "$(hostpath "$here"):/w" aces-builder:latest bash -lc '
  set -euo pipefail
  /usr/bin/python3 -m venv /tmp/v
  /tmp/v/bin/pip install -q --disable-pip-version-check --only-binary=:all: -r /w/requirements.in
  echo "resolved with $(/usr/bin/python3 --version), $(/tmp/v/bin/pip --version | cut -d" " -f1-2)" >&2
  /tmp/v/bin/pip freeze > /tmp/freeze.txt
  /usr/bin/python3 /w/lock.py /w/requirements.in /tmp/freeze.txt > /w/requirements.txt.new
'
mv "$here/requirements.txt.new" "$here/requirements.txt"
echo "lock.sh: $(grep -c '==' "$here/requirements.txt") distributions pinned in requirements.txt"
