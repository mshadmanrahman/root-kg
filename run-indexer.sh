#!/bin/zsh
# ROOT indexer wrapper for launchd.
# Secret loading order (first hit wins):
#   1. ~/.config/anthropic/env (canonical, chmod 600)
#   2. $ROOT_DIR/.env (legacy, gitignored)
# The launchd plist no longer carries ANTHROPIC_API_KEY inline.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${ROOT_DIR}/.venv/bin/python"
SECRET_FILE="${HOME}/.config/anthropic/env"

if [[ -f "$SECRET_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$SECRET_FILE"
    set +a
elif [[ -f "${ROOT_DIR}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${ROOT_DIR}/.env"
    set +a
fi

cd "$ROOT_DIR"

if [[ -z "${ANTHROPIC_API_KEY:-}" || "${ANTHROPIC_API_KEY}" == ROTATE_ME* ]]; then
    echo "ERROR: ANTHROPIC_API_KEY missing or placeholder. Paste a freshly rotated key into ${SECRET_FILE}." >&2
    exit 1
fi

exec "$PYTHON" indexer.py --extract
