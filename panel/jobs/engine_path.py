from __future__ import annotations

import sys
from pathlib import Path

from django.conf import settings


def ensure_repo_on_path() -> Path:
    root = Path(settings.PANEL_REPO_ROOT)
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return root
