from __future__ import annotations

from django.db import models
from django.utils.text import slugify


class Niche(models.Model):
    class Aspect(models.TextChoices):
        PORTRAIT = "9:16", "Short 9:16"
        LANDSCAPE = "16:9", "Landscape 16:9"
        SQUARE = "1:1", "Square 1:1"

    name = models.CharField("Nome", max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="Nicho pai (subnicho)",
    )
    briefing = models.TextField(
        "Briefing do nicho",
        blank=True,
        help_text="Tom, público, o que evitar, exemplos de temas.",
    )
    keywords = models.TextField(
        "Keywords de pesquisa",
        blank=True,
        help_text="Uma por linha. Usadas na pesquisa YouTube e nas sugestões.",
    )
    default_voice = models.CharField(
        "Voz TTS padrão",
        max_length=120,
        blank=True,
        help_text="Ex.: pt-BR-FranciscaNeural-Female",
    )
    default_language = models.CharField(
        "Idioma do roteiro",
        max_length=20,
        default="pt-BR",
    )
    default_aspect = models.CharField(
        "Aspecto padrão",
        max_length=10,
        choices=Aspect.choices,
        default=Aspect.PORTRAIT,
    )
    default_video_source = models.CharField(
        "Fonte de material (Create)",
        max_length=20,
        default="pexels",
        help_text="pexels | pixabay | coverr | local",
    )
    paragraph_number = models.PositiveSmallIntegerField(
        "Parágrafos do roteiro",
        default=1,
        help_text="1–3 para short; 5–10 para vídeo mais longo.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Nicho"
        verbose_name_plural = "Nichos"
        ordering = ["name"]

    def __str__(self) -> str:
        if self.parent_id:
            return f"{self.parent.name} › {self.name}"
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name) or f"nicho-{self.pk or 'novo'}"
        super().save(*args, **kwargs)

    def keyword_list(self) -> list[str]:
        return [line.strip() for line in self.keywords.splitlines() if line.strip()]
