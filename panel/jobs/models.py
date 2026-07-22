from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils import timezone

from panel.channels.models import YouTubeChannel
from panel.niches.models import Niche


class Job(models.Model):
    class JobType(models.TextChoices):
        CREATE = "create", "Create (stock + TTS)"
        CLIP = "clip", "Clip (cortes YouTube)"
        DUB = "dub", "Dub (EN → PT)"
        RESEARCH = "research", "Pesquisa de nicho"

    class Status(models.TextChoices):
        DRAFT = "draft", "Rascunho"
        QUEUED = "queued", "Na fila"
        RUNNING = "running", "Executando"
        AWAITING_REVIEW = "awaiting_review", "Aguardando revisão"
        APPROVED = "approved", "Aprovado"
        UPLOADING = "uploading", "Enviando ao YouTube"
        PUBLISHED = "published", "Publicado"
        FAILED = "failed", "Falhou"
        CANCELLED = "cancelled", "Cancelado"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    niche = models.ForeignKey(Niche, on_delete=models.CASCADE, related_name="jobs")
    channel = models.ForeignKey(
        YouTubeChannel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
    )
    job_type = models.CharField(max_length=20, choices=JobType.choices)
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    subject = models.CharField(
        "Tema / assunto",
        max_length=300,
        blank=True,
        help_text="Create: tema do short. Clip/Dub: assunto do corte ou dublagem.",
    )
    source_url = models.URLField(
        "URL de origem (Clip/Dub)",
        blank=True,
        help_text="Link do YouTube ou arquivo remoto.",
    )
    cut_topic = models.CharField(
        "Assunto do corte (Clip)",
        max_length=300,
        blank=True,
        help_text="Ex.: 'momento engraçado', 'dica de finanças'.",
    )
    target_duration_sec = models.PositiveIntegerField(
        "Duração alvo do corte (s)",
        default=45,
    )
    script_override = models.TextField("Roteiro manual (opcional)", blank=True)
    output_title = models.CharField("Título YouTube", max_length=100, blank=True)
    output_description = models.TextField("Descrição YouTube", blank=True)
    privacy_status = models.CharField(
        max_length=20,
        default="private",
        help_text="private | unlisted | public",
    )
    engine_task_id = models.CharField(max_length=64, blank=True)
    work_dir = models.CharField(max_length=500, blank=True)
    output_video = models.CharField(max_length=500, blank=True)
    youtube_video_id = models.CharField(max_length=64, blank=True)
    progress = models.PositiveSmallIntegerField(default=0)
    log = models.TextField(blank=True)
    error = models.TextField(blank=True)
    result_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Job"
        verbose_name_plural = "Jobs"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"[{self.get_job_type_display()}] {self.subject or self.source_url or self.public_id}"

    def append_log(self, message: str) -> None:
        stamp = timezone.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{stamp}] {message}"
        self.log = f"{self.log}\n{line}".strip()
        self.save(update_fields=["log", "updated_at"])

    def ensure_work_dir(self) -> Path:
        root = Path(settings.PANEL_STORAGE_ROOT) / self.niche.slug / str(self.public_id)
        root.mkdir(parents=True, exist_ok=True)
        if not self.work_dir:
            self.work_dir = str(root)
            self.save(update_fields=["work_dir", "updated_at"])
        return root

    def mark_running(self) -> None:
        self.status = self.Status.RUNNING
        self.started_at = timezone.now()
        self.error = ""
        self.save(update_fields=["status", "started_at", "error", "updated_at"])

    def mark_failed(self, error: str) -> None:
        self.status = self.Status.FAILED
        self.error = error
        self.finished_at = timezone.now()
        self.append_log(f"FAILED: {error}")
        self.save(
            update_fields=["status", "error", "finished_at", "updated_at", "log"]
        )

    def mark_awaiting_review(self, output_video: str, **extra) -> None:
        self.status = self.Status.AWAITING_REVIEW
        self.output_video = output_video
        self.progress = 100
        self.finished_at = timezone.now()
        if extra:
            data = dict(self.result_json or {})
            data.update(extra)
            self.result_json = data
        self.append_log(f"Pronto para revisão: {output_video}")
        self.save()
