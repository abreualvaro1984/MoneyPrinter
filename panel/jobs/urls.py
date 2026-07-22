from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="jobs_health"),
]
