#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "panel.config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Install panel deps with: "
            "uv sync --extra panel"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
