from __future__ import annotations

import json

from django.conf import settings
from django.db import models

from panel.niches.models import Niche


class YouTubeChannel(models.Model):
    class Status(models.TextChoices):
        DISCONNECTED = "disconnected", "Desconectado"
        CONNECTED = "connected", "Conectado"
        ERROR = "error", "Erro"

    niche = models.OneToOneField(
        Niche,
        on_delete=models.CASCADE,
        related_name="youtube_channel",
        verbose_name="Nicho",
    )
    title = models.CharField("Título do canal", max_length=200, blank=True)
    channel_id = models.CharField("YouTube Channel ID", max_length=64, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DISCONNECTED,
    )
    token_json = models.TextField(
        "OAuth token (JSON)",
        blank=True,
        help_text="Preenchido automaticamente após o OAuth.",
    )
    last_error = models.TextField(blank=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Canal YouTube"
        verbose_name_plural = "Canais YouTube"

    def __str__(self) -> str:
        label = self.title or self.channel_id or "sem canal"
        return f"{self.niche.name} → {label}"

    def get_token_data(self) -> dict:
        if not self.token_json:
            return {}
        return json.loads(self.token_json)

    def set_token_data(self, data: dict) -> None:
        self.token_json = json.dumps(data)

    @property
    def is_ready(self) -> bool:
        return self.status == self.Status.CONNECTED and bool(self.token_json)

    def credentials_path(self) -> str:
        return str(
            settings.BASE_DIR
            / "credentials"
            / f"youtube_token_{self.niche.slug}.json"
        )
