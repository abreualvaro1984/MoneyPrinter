from __future__ import annotations

from django.apps import AppConfig


class JobsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "panel.jobs"
    label = "jobs"
    verbose_name = "Jobs"
