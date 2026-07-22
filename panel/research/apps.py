from __future__ import annotations

from django.apps import AppConfig


class ResearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "panel.research"
    label = "research"
    verbose_name = "Pesquisa"
