from __future__ import annotations

from django.db import models

from panel.niches.models import Niche


class LlmCredential(models.Model):
    """API key de IA cadastrada no painel (várias por provider)."""

    name = models.CharField(
        "Nome amigável",
        max_length=120,
        help_text="Ex.: Moonshot produção, OpenAI barata",
    )
    provider = models.CharField(
        "Provider",
        max_length=40,
        help_text="moonshot, openai, gemini, deepseek, …",
    )
    api_key = models.TextField("API key")
    base_url = models.CharField(max_length=300, blank=True)
    model_name = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(
        "Padrão para Trends",
        default=False,
        help_text="Usada quando nenhuma IA for escolhida na pesquisa.",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "provider", "name"]
        verbose_name = "Credencial de IA"
        verbose_name_plural = "Credenciais de IA"
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "name"],
                name="uniq_llm_credential_provider_name",
            ),
        ]

    def __str__(self) -> str:
        flag = " ★" if self.is_default else ""
        return f"{self.name} ({self.provider}){flag}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            type(self).objects.filter(is_default=True).exclude(pk=self.pk).update(
                is_default=False
            )

    @property
    def api_key_masked(self) -> str:
        key = (self.api_key or "").strip()
        if len(key) <= 8:
            return "••••"
        return f"{key[:4]}…{key[-4:]}"


class TrendRun(models.Model):
    """Uma execução de pesquisa de trends (área Trends)."""

    niche = models.ForeignKey(Niche, on_delete=models.CASCADE, related_name="trend_runs")
    platforms = models.JSONField(default=list, blank=True)
    summary_pt = models.TextField(blank=True)
    topics_json = models.JSONField(default=list, blank=True)
    candidates_json = models.JSONField(default=list, blank=True)
    llm_credential = models.ForeignKey(
        "ui.LlmCredential",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trend_runs",
        verbose_name="IA usada",
    )
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Execução de trends"
        verbose_name_plural = "Execuções de trends"

    def __str__(self) -> str:
        return f"Trends #{self.pk} · {self.niche}"


class ScriptDraft(models.Model):
    """Rascunho de roteiro (área Roteiros). Não gera vídeo sozinho."""

    class AiStatus(models.TextChoices):
        UNKNOWN = "unknown", "Desconhecido"
        PASS = "pass", "Humano / OK"
        REVIEW = "review", "Revisar"
        REGEN = "regen", "Regenerar (muito IA)"
        SKIPPED = "skipped", "Detector não configurado"

    niche = models.ForeignKey(Niche, on_delete=models.CASCADE, related_name="script_drafts")
    trend_run = models.ForeignKey(
        TrendRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scripts",
    )
    topic = models.CharField(max_length=300)
    title = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)
    hooks = models.TextField(blank=True)
    cta = models.TextField(blank=True)
    hashtags = models.CharField(max_length=500, blank=True)
    version = models.PositiveIntegerField(default=1)
    ai_score = models.FloatField(
        null=True,
        blank=True,
        help_text="0–100 probabilidade de IA (quanto maior, mais 'IA')",
    )
    ai_status = models.CharField(
        max_length=20, choices=AiStatus.choices, default=AiStatus.UNKNOWN
    )
    ai_raw = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Rascunho de roteiro"
        verbose_name_plural = "Rascunhos de roteiro"

    def __str__(self) -> str:
        return f"{self.title or self.topic} (v{self.version})"

    def mark_scored(self, score: float | None, status: str, raw: dict | None = None) -> None:
        self.ai_score = score
        self.ai_status = status
        if raw is not None:
            self.ai_raw = raw
        self.save(update_fields=["ai_score", "ai_status", "ai_raw", "updated_at"])
