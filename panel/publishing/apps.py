from __future__ import annotations

from django.apps import AppConfig


class PublishingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "panel.publishing"
    label = "publishing"
    verbose_name = "Publicação multi-plataforma"
