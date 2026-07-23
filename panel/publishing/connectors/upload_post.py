from __future__ import annotations

import os
from pathlib import Path

import requests

from panel.publishing.connectors.base import PublishResult, validate_metadata
from panel.publishing.models import SocialAccount


class UploadPostConnector:
    """
    Gateway Upload-Post para TikTok / Instagram / YouTube Shorts.
    Credenciais por conta: { "api_key": "...", "username": "..." }
    ou herda de settings/env UPLOAD_POST_*.
    """

    API_BASE = "https://api.upload-post.com"

    def __init__(self, platform: str):
        self.platform = platform

    def _creds(self, account: SocialAccount) -> dict:
        data = account.get_credentials()
        api_key = data.get("api_key") or os.environ.get("UPLOAD_POST_API_KEY", "")
        username = (
            data.get("username")
            or account.username
            or os.environ.get("UPLOAD_POST_USERNAME", "")
        )
        return {"api_key": api_key, "username": username}

    def validate_account(self, account: SocialAccount) -> tuple[bool, str]:
        if account.platform != self.platform:
            return False, f"Conta não é {self.platform}"
        creds = self._creds(account)
        if not creds["api_key"] or not creds["username"]:
            return False, "Informe api_key e username do Upload-Post na conta"
        return True, ""

    def upload(
        self, account: SocialAccount, video_path: str, metadata: dict
    ) -> PublishResult:
        missing = validate_metadata(self.platform, metadata)
        # Upload-Post uses title/caption loosely
        if self.platform == "instagram" and "caption" in missing:
            if metadata.get("description") or metadata.get("title"):
                missing = [m for m in missing if m != "caption"]
        if missing:
            return PublishResult(False, error=f"Campos obrigatórios faltando: {missing}")
        if not Path(video_path).is_file():
            return PublishResult(False, error=f"Arquivo não encontrado: {video_path}")

        ok, err = self.validate_account(account)
        if not ok:
            return PublishResult(False, error=err)

        creds = self._creds(account)
        title = (
            metadata.get("title")
            or metadata.get("caption")
            or metadata.get("description")
            or account.name
        )
        privacy = metadata.get("privacy") or "PUBLIC_TO_EVERYONE"
        platform_name = "youtube" if self.platform == "youtube" else self.platform

        try:
            with open(video_path, "rb") as video_file:
                data = [
                    ("user", creds["username"]),
                    ("title", str(title)[:2200]),
                    ("privacy_level", privacy),
                    ("platform[]", platform_name),
                ]
                if self.platform == "youtube":
                    data.append(("youtube_title", str(metadata.get("title") or title)[:100]))
                    data.append(
                        (
                            "youtube_description",
                            str(metadata.get("description") or ""),
                        )
                    )
                    for tag in metadata.get("tags") or []:
                        data.append(("tags[]", str(tag)))
                    data.append(
                        ("privacyStatus", metadata.get("privacy") or account.default_privacy or "public")
                    )
                    data.append(("containsSyntheticMedia", "true"))

                response = requests.post(
                    f"{self.API_BASE}/api/upload",
                    headers={"Authorization": f"Apikey {creds['api_key']}"},
                    data=data,
                    files={"video": video_file},
                    timeout=300,
                )
                response.raise_for_status()
                result = response.json()
        except Exception as exc:
            return PublishResult(False, error=f"{type(exc).__name__}: {exc}")

        success = bool(result.get("success"))
        request_id = str(result.get("request_id") or "")
        return PublishResult(
            success=success,
            remote_id=request_id,
            remote_url="",
            raw=result if isinstance(result, dict) else {"result": result},
            error="" if success else str(result.get("message") or result.get("error") or "falha Upload-Post"),
        )


class TikTokConnector(UploadPostConnector):
    def __init__(self):
        super().__init__("tiktok")


class InstagramConnector(UploadPostConnector):
    def __init__(self):
        super().__init__("instagram")


class FacebookConnector(UploadPostConnector):
    """Facebook via Upload-Post quando disponível; senão exige Page token futuro."""

    def __init__(self):
        super().__init__("facebook")
