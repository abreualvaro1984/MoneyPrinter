from __future__ import annotations

from pathlib import Path

from panel.publishing.connectors.base import PublishResult, validate_metadata
from panel.publishing.models import SocialAccount


class KwaiConnector:
    """
    Kwai ainda não tem upload API estável para criadores no fluxo deste painel.
    O conector valida metadados e registra o pacote como 'manual' para o operador publicar.
    """

    platform = "kwai"

    def validate_account(self, account: SocialAccount) -> tuple[bool, str]:
        if account.platform != self.platform:
            return False, "Conta não é Kwai"
        if not account.username and not account.external_id:
            return False, "Informe username ou ID da conta Kwai"
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

        # Staging: copy pointer into work notes via raw payload; operator uploads manually.
        return PublishResult(
            success=True,
            remote_id="manual-pending",
            remote_url="",
            raw={
                "mode": "manual",
                "account": account.username or account.external_id,
                "video_path": video_path,
                "metadata": metadata,
                "message": (
                    "Kwai: publicação automática indisponível. "
                    "Metadados validados — publique o arquivo manualmente no app."
                ),
            },
            error="",
        )
