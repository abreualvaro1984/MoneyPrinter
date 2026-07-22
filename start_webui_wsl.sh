#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/Projects/MoneyPrinterTurbo"
sed -i 's/\r$//' webui.sh 2>/dev/null || true
export MPT_WEBUI_HOST=127.0.0.1
export MPT_WEBUI_PORT=8501
exec sh webui.sh
