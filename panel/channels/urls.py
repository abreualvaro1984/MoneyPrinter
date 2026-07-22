from __future__ import annotations

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect
from django.urls import path

from . import youtube as youtube_service


@staff_member_required
def oauth_callback(request):
    code = request.GET.get("code")
    state = request.GET.get("state")
    error = request.GET.get("error")
    if error:
        messages.error(request, f"OAuth negado: {error}")
        return redirect("admin:channels_youtubechannel_changelist")
    try:
        channel = youtube_service.finish_authorization(code=code, state=state)
        messages.success(request, f"Canal conectado: {channel}")
        return redirect("admin:channels_youtubechannel_change", channel.pk)
    except Exception as exc:
        messages.error(request, f"Falha no callback OAuth: {exc}")
        return redirect("admin:channels_youtubechannel_changelist")


urlpatterns = [
    path("oauth/callback/", oauth_callback, name="youtube_oauth_callback"),
]
