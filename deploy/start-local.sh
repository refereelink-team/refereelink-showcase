#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${SHOWCASE_ENV_FILE:-$root/deploy/showcase.env}"
if [[ -f "$env_file" ]]; then
  set -a
  source "$env_file"
  set +a
elif [[ -n "${SHOWCASE_ENV_FILE:-}" ]]; then
  printf 'Configured SHOWCASE_ENV_FILE does not exist: %s\n' "$env_file" >&2
  exit 1
fi

python="${SHOWCASE_PYTHON:-$root/.venv/bin/python}"
if [[ ! -x "$python" ]]; then
  printf 'Backend Python not found: %s\nCreate .venv with Python 3.12+ or set SHOWCASE_PYTHON to your CUDA environment interpreter.\n' "$python" >&2
  exit 1
fi

cd -- "$root"
export PYTHONDONTWRITEBYTECODE=1
exec "$python" -m uvicorn showcase.app:app --app-dir "$root/server" \
  --host "${SHOWCASE_HOST:-127.0.0.1}" --port "${SHOWCASE_PORT:-8010}"
