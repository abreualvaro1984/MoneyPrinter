from __future__ import annotations

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("channels/", include("panel.channels.urls")),
    path("jobs/", include("panel.jobs.urls")),
    path("research/", include("panel.research.urls")),
]
