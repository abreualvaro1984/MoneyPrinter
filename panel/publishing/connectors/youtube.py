from __future__ import annotations

from pathlib import Path

from panel.channels import youtube as yt
from panel.publishing.connectors.base import PublishResult, validate_metadata
from panel.publishing.models import SocialAccount


class YouTubeConnector:
    platform = "youtube"

    def validate_account(self, account: SocialAccount) -> tuple[bool, str]:
        if account.platform != self.platform:
            return False, "Conta não é YouTube"
        creds = account.get_credentials()
        if not creds.get("token") and not creds.get("refresh_token"):
            return False, "Sem token OAuth YouTube"
        return True, ""

    def upload(
        self, account: SocialAccount, video_path: str, metadata: dict
    ) -> PublishResult:
        missing = validate_metadata(self.platform, metadata)
        if missing:
            return PublishResult(False, error=f"Campos obrigatórios faltando: {missing}")
        if not Path(video_path).is_file():
            return PublishResult(False, error=f"Arquivo não encontrado: {video_path}")

        ok, err = self.validate_account(account)
        if not ok:
            return PublishResult(False, error=err)

        privacy = metadata.get("privacy") or account.default_privacy or "private"

        def _persist(data: dict) -> None:
            account.set_credentials(data)
            account.save(update_fields=["credentials_json", "updated_at"])

        try:
            response = yt.upload_video_with_token_data(
                account.get_credentials(),
                video_path,
                title=str(metadata.get("title") or "")[:100],
                description=str(metadata.get("description") or ""),
                tags=list(metadata.get("tags") or []),
                privacy_status=str(privacy),
                category_id=str(metadata.get("category_id") or "22"),
                made_for_kids=bool(metadata.get("made_for_kids", False)),
                on_token_refresh=_persist,
            )
        except Exception as exc:
            return PublishResult(False, error=f"{type(exc).__name__}: {exc}")

        video_id = response.get("id", "")
        return PublishResult(
            success=bool(video_id),
            remote_id=video_id,
            remote_url=f"https://youtu.be/{video_id}" if video_id else "",
            raw=response or {},
            error="" if video_id else "Upload YouTube sem id",
        )
