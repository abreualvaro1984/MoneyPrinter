from __future__ import annotations

import time
from dataclasses import dataclass

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from panel.channels.youtube import resolve_youtube_api_key
from panel.ui.models import YoutubeDataApiKey


@dataclass(frozen=True)
class YoutubeTestResult:
    ok: bool
    message: str
    elapsed: float


def test_youtube_api_key(api_key: str | None = None) -> YoutubeTestResult:
    """
    Valida a YouTube Data API key com uma chamada barata (mostPopular, 1 item).
    Se api_key for None/vazia, usa banco → env.
    """
    key = (api_key or "").strip()
    source = "form"
    if not key:
        key, source = resolve_youtube_api_key()
    if not key:
        return YoutubeTestResult(
            False,
            "Cole a YouTube API key (ou salve em /apis/) antes de testar.",
            0.0,
        )
    if key.upper().startswith("GOCSPX"):
        return YoutubeTestResult(
            False,
            "Isso é client secret OAuth (GOCSPX-…), não API key. Use uma key AIza…",
            0.0,
        )

    started = time.perf_counter()
    try:
        youtube = build("youtube", "v3", developerKey=key, cache_discovery=False)
        data = (
            youtube.videos()
            .list(part="id", chart="mostPopular", regionCode="BR", maxResults=1)
            .execute()
        )
        elapsed = time.perf_counter() - started
        items = data.get("items") or []
        if not items:
            return YoutubeTestResult(
                False,
                f"API respondeu sem itens ({source}). Verifique quotas/região.",
                elapsed,
            )
        return YoutubeTestResult(
            True,
            f"YouTube OK — key válida ({source}), respondeu em {elapsed:.1f}s",
            elapsed,
        )
    except HttpError as exc:
        elapsed = time.perf_counter() - started
        status = getattr(exc.resp, "status", "?")
        detail = str(exc)[:240]
        hint = ""
        if status in (400, 403):
            hint = " Confira se a YouTube Data API v3 está ativada e se a key é AIza…"
        return YoutubeTestResult(
            False,
            f"HTTP {status}: {detail}.{hint}",
            elapsed,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - started
        return YoutubeTestResult(
            False,
            f"{type(exc).__name__}: {exc}",
            elapsed,
        )


def test_saved_youtube_key() -> YoutubeTestResult:
    """Testa a key salva no banco (ou fallback env)."""
    return test_youtube_api_key(YoutubeDataApiKey.get_api_key() or None)
