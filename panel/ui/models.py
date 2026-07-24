from __future__ import annotations

from django.db import models

from panel.niches.models import Niche


class YoutubeDataApiKey(models.Model):
    """
    API key da YouTube Data API v3 (pesquisa / trends / nichos).
    Singleton (pk=1) — o usuário edita na UI /apis/, não no .env.
    """

    api_key = models.TextField("YouTube API key", blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "YouTube Data API key"
        verbose_name_plural = "YouTube Data API keys"

    def __str__(self) -> str:
        return "YouTube Data API key"

    @classmethod
    def get_solo(cls) -> YoutubeDataApiKey:
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    def get_api_key(cls) -> str:
        try:
            return (cls.get_solo().api_key or "").strip()
        except Exception:
            return ""

    @property
    def api_key_masked(self) -> str:
        key = (self.api_key or "").strip()
        if not key:
            return ""
        if len(key) <= 8:
            return "••••"
        return f"{key[:4]}…{key[-4:]}"


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
        help_text="moonshot, openai, gemini, grok, deepseek, …",
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


class NicheDiscoveryRun(models.Model):
    """Resultado de pesquisa de nichos/subnichos pela IA (SQLite)."""

    class Kind(models.TextChoices):
        ROOT = "root", "Nichos principais"
        SUB = "sub", "Subnichos"

    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.ROOT)
    parent_niche = models.ForeignKey(
        Niche,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="discovery_runs",
    )
    llm_credential = models.ForeignKey(
        LlmCredential,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="niche_discoveries",
    )
    summary_pt = models.TextField(blank=True)
    suggestions_json = models.JSONField(default=list, blank=True)
    signals_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Sinais brutos do YouTube (trending/buscas) usados na descoberta.",
    )
    video_format = models.CharField(
        "Formato de vídeo",
        max_length=20,
        default="dark",
        blank=True,
        help_text="dark | sleep | blackscreen | ambient | face | hybrid | screen | any",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Descoberta de nichos"
        verbose_name_plural = "Descobertas de nichos"

    def __str__(self) -> str:
        return f"Discovery #{self.pk} ({self.kind})"


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
    llm_credential = models.ForeignKey(
        "ui.LlmCredential",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="script_drafts",
        verbose_name="IA usada",
    )
    target_duration_sec = models.PositiveIntegerField(
        "Duração alvo (segundos)",
        default=60,
        help_text="Alvo falado do roteiro; o texto pode ficar uns segundos a mais ou a menos.",
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


class VideoPlan(models.Model):
    """Plano de vídeo: roteiro + assets + voz + dublagem (não renderiza sozinho)."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Rascunho"
        READY = "ready", "Pronto"

    niche = models.ForeignKey(
        Niche, on_delete=models.CASCADE, related_name="video_plans"
    )
    llm_credential = models.ForeignKey(
        LlmCredential,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="video_plans",
        verbose_name="IA usada",
    )
    video_format = models.CharField(
        "Formato de vídeo",
        max_length=20,
        default="dark",
        blank=True,
    )
    topic = models.CharField("Tema", max_length=300, blank=True)
    title = models.CharField("Título", max_length=200, blank=True)
    script_body = models.TextField("Roteiro", blank=True)
    voice_name = models.CharField(
        "Voz TTS",
        max_length=120,
        blank=True,
        help_text="Ex.: pt-BR-FranciscaNeural-Female",
    )
    voice_notes = models.TextField("Notas da voz", blank=True)
    assets_json = models.JSONField(default=list, blank=True)
    dub_suggestions_json = models.JSONField(default=list, blank=True)
    plan_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    script_draft = models.ForeignKey(
        ScriptDraft,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="from_plans",
        verbose_name="Roteiro vinculado",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Plano de vídeo"
        verbose_name_plural = "Planos de vídeo"

    def __str__(self) -> str:
        return f"Plano #{self.pk} · {self.title or self.topic or self.niche}"
