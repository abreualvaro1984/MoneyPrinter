from __future__ import annotations

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from panel.ui import views as ui_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("cadastro/", ui_views.register, name="register"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("panel.ui.urls")),
    path("channels/", include("panel.channels.urls")),
    path("jobs/", include("panel.jobs.urls")),
    path("research/", include("panel.research.urls")),
]
