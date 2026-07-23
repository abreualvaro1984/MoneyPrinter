from __future__ import annotations

import json

from django.db import models
from django.utils import timezone

from panel.niches.models import Niche
from panel.publishing.catalog import PLATFORM_SPECS


class SocialAccount(models.Model):
    """Conta em uma plataforma de vídeo. Várias contas por plataforma são permitidas."""

    class Platform(models.TextChoices):
        YOUTUBE = "youtube", "YouTube"
        TIKTOK = "tiktok", "TikTok"
        INSTAGRAM = "instagram", "Instagram Reels"
        FACEBOOK = "facebook", "Facebook"
        KWAI = "kwai", "Kwai"

    class Status(models.TextChoices):
        DRAFT = "draft", "Rascunho"
        CONNECTED = "connected", "Conectada"
        ERROR = "error", "Erro"
        DISABLED = "disabled", "Desativada"

    class AuthMode(models.TextChoices):
        OAUTH = "oauth", "OAuth"
        TOKEN = "token", "Access token / API key"
        UPLOAD_POST = "upload_post", "Gateway Upload-Post"
        MANUAL = "manual", "Manual (sem API)"

    name = models.CharField(
        "Nome amigável",
        max_length=120,
        help_text="Ex.: YT Finanças BR, TikTok Curiosidades 2",
    )
    platform = models.CharField(max_length=20, choices=Platform.choices, db_index=True)
    niche = models.ForeignKey(
        Niche,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="social_accounts",
        verbose_name="Nicho (opcional)",
    )
    external_id = models.CharField(
        "ID externo (canal/página/@)",
        max_length=128,
        blank=True,
        help_text="YouTube channelId, IG business id, Page id, @handle, etc.",
    )
    username = models.CharField("Username / handle", max_length=128, blank=True)
    auth_mode = models.CharField(
        max_length=20,
        choices=AuthMode.choices,
        default=AuthMode.OAUTH,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    credentials_json = models.TextField(
        "Credenciais (JSON)",
        blank=True,
        help_text="Tokens OAuth, access_token, refresh_token, upload_post user, etc. Não versionar.",
    )
    default_privacy = models.CharField(max_length=40, default="private", blank=True)
    notes = models.TextField(blank=True)
    last_error = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Conta social"
        verbose_name_plural = "Contas sociais"
        ordering = ["platform", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["platform", "name"],
                name="uniq_social_account_platform_name",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.get_platform_display()}] {self.name}"

    def get_credentials(self) -> dict:
        if not self.credentials_json:
            return {}
        return json.loads(self.credentials_json)

    def set_credentials(self, data: dict) -> None:
        self.credentials_json = json.dumps(data, ensure_ascii=False)

    @property
    def is_ready(self) -> bool:
        if not self.is_active or self.status != self.Status.CONNECTED:
            return False
        if self.auth_mode == self.AuthMode.MANUAL:
            return True
        return bool(self.credentials_json)

    def platform_spec(self):
        return PLATFORM_SPECS.get(self.platform)


class PublishTarget(models.Model):
    """Destino de publicação de um Job: conta + metadados preenchidos."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pendente"
        READY = "ready", "Pronto"
        UPLOADING = "uploading", "Enviando"
        PUBLISHED = "published", "Publicado"
        FAILED = "failed", "Falhou"
        SKIPPED = "skipped", "Ignorado"

    job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.CASCADE,
        related_name="publish_targets",
    )
    account = models.ForeignKey(
        SocialAccount,
        on_delete=models.PROTECT,
        related_name="publish_targets",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    # Metadados tipados + JSON livre para campos específicos da plataforma
    title = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    tags = models.CharField(
        max_length=500,
        blank=True,
        help_text="Separadas por vírgula",
    )
    hashtags = models.CharField(max_length=500, blank=True)
    privacy = models.CharField(max_length=40, blank=True)
    language = models.CharField(max_length=20, default="pt-BR", blank=True)
    category_id = models.CharField(max_length=20, blank=True)
    made_for_kids = models.BooleanField(default=False)
    thumbnail_path = models.CharField(max_length=500, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    extra_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Campos extras por plataforma (cover_time_ms, share_to_feed, etc.)",
    )
    remote_id = models.CharField(
        "ID remoto do post/vídeo", max_length=128, blank=True
    )
    remote_url = models.URLField(blank=True)
    error = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Destino de publicação"
        verbose_name_plural = "Destinos de publicação"
        unique_together = [("job", "account")]

    def __str__(self) -> str:
        return f"{self.job_id} → {self.account}"

    def tag_list(self) -> list[str]:
        return [t.strip() for t in self.tags.split(",") if t.strip()]

    def hashtag_list(self) -> list[str]:
        items = []
        for raw in self.hashtags.replace(",", " ").split():
            tag = raw.strip()
            if not tag:
                continue
            if not tag.startswith("#"):
                tag = f"#{tag}"
            items.append(tag)
        return items

    def to_metadata(self) -> dict:
        meta = {
            "title": self.title,
            "description": self.description,
            "caption": self.description or self.title,
            "tags": self.tag_list(),
            "hashtags": self.hashtag_list(),
            "privacy": self.privacy or self.account.default_privacy or "private",
            "language": self.language or "pt-BR",
            "category_id": self.category_id or "22",
            "made_for_kids": self.made_for_kids,
            "thumbnail_path": self.thumbnail_path,
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
        }
        meta.update(self.extra_json or {})
        return meta

    def mark_published(self, remote_id: str = "", remote_url: str = "") -> None:
        self.status = self.Status.PUBLISHED
        self.remote_id = remote_id or self.remote_id
        self.remote_url = remote_url or self.remote_url
        self.published_at = timezone.now()
        self.error = ""
        self.save()

    def mark_failed(self, error: str) -> None:
        self.status = self.Status.FAILED
        self.error = error
        self.save(update_fields=["status", "error", "updated_at"])
