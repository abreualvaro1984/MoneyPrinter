#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
cd /mnt/f/Projetos/MoneyPrinter/panel
uv run python scripts/verify_seed.py
uv run python manage.py test panel.jobs.tests panel.niches -v1
