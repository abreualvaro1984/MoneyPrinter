#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
sed -i 's/\r$//' panel/manage.py 2>/dev/null || true
test -f panel/.env || cp panel/.env.example panel/.env
mkdir -p panel/credentials storage/niches
uv sync --extra panel
cd panel
uv run python manage.py migrate --noinput
uv run python manage.py bootstrap_panel
echo "Admin: http://127.0.0.1:8000/admin/  (admin / admin)"
echo "Em outro terminal: uv run python manage.py process_jobs --loop"
exec uv run python manage.py runserver 127.0.0.1:8000
