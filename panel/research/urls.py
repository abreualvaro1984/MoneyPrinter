from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("run/<slug:niche_slug>/", views.run_research, name="research_run"),
]
