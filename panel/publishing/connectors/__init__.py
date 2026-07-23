from __future__ import annotations

from panel.publishing.connectors.kwai import KwaiConnector
from panel.publishing.connectors.upload_post import (
    FacebookConnector,
    InstagramConnector,
    TikTokConnector,
)
from panel.publishing.connectors.youtube import YouTubeConnector
from panel.publishing.models import SocialAccount

_REGISTRY = {
    "youtube": YouTubeConnector(),
    "tiktok": TikTokConnector(),
    "instagram": InstagramConnector(),
    "facebook": FacebookConnector(),
    "kwai": KwaiConnector(),
}


def get_connector(platform: str):
    try:
        return _REGISTRY[platform]
    except KeyError as exc:
        raise KeyError(f"Sem conector para plataforma: {platform}") from exc


def connector_for_account(account: SocialAccount):
    # Prefer Upload-Post connector when account auth_mode says so
    if account.auth_mode == SocialAccount.AuthMode.UPLOAD_POST:
        if account.platform == "youtube":
            from panel.publishing.connectors.upload_post import UploadPostConnector

            return UploadPostConnector("youtube")
        return get_connector(account.platform)
    if account.platform == "youtube" and account.auth_mode == SocialAccount.AuthMode.OAUTH:
        return get_connector("youtube")
    return get_connector(account.platform)
