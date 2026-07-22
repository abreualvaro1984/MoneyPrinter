from __future__ import annotations

from django.apps import AppConfig


class NichesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "panel.niches"
    label = "niches"
    verbose_name = "Nichos"
