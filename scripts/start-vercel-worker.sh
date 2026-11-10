#!/usr/bin/env bash
set -euo pipefail

release="$(realpath "$1")"
data_dir=/vercel/sandbox/cro-auditor-data
mkdir -p "$data_dir"
exec 9>"$data_dir/worker.lock"
flock -n 9 || exit 0

export CRO_DATA_DIR="$data_dir"
export PLAYWRIGHT_BROWSERS_PATH="$data_dir/chromium"
export MAX_PAGES=3
venv="$data_dir/venv"
requirements_hash="$(sha256sum "$release/requirements.txt" | cut -d' ' -f1)"
if [[ "$(cat "$data_dir/requirements.sha256" 2>/dev/null || true)" != "$requirements_hash" ]]; then
    python3 -m venv "$venv"
    "$venv/bin/pip" install --disable-pip-version-check -r "$release/requirements.txt"
    "$venv/bin/python" -m playwright install --with-deps chromium
    printf '%s\n' "$requirements_hash" > "$data_dir/requirements.sha256"
fi

exec "$venv/bin/python" -u "$release/app.py" --host 0.0.0.0 --port 8787 --no-browser
