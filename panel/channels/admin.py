from __future__ import annotations

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html

from .models import YouTubeChannel
from . import youtube as youtube_service


@admin.register(YouTubeChannel)
class YouTubeChannelAdmin(admin.ModelAdmin):
    change_form_template = "admin/channels/youtubechannel/change_form.html"
    list_display = ("niche", "title", "channel_id", "status", "oauth_link", "updated_at")
    list_filter = ("status",)
    search_fields = ("title", "channel_id", "niche__name")
    readonly_fields = ("status", "title", "channel_id", "connected_at", "last_error", "token_json")
    fieldsets = (
        (
            None,
            {
                "fields": ("niche",),
                "description": (
                    "Conecte o canal criador via OAuth (upload). "
                    "Para pesquisa de nichos/trends, cadastre a API key em /apis/."
                ),
            },
        ),
        (
            "Status do canal",
            {
                "fields": (
                    "status",
                    "title",
                    "channel_id",
                    "connected_at",
                    "last_error",
                    "token_json",
                ),
            },
        ),
    )

    def oauth_link(self, obj: YouTubeChannel) -> str:
        url = reverse("admin:channels_youtubechannel_oauth", args=[obj.pk])
        return format_html('<a class="button" href="{}">Conectar OAuth</a>', url)

    oauth_link.short_description = "OAuth"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/oauth/",
                self.admin_site.admin_view(self.start_oauth),
                name="channels_youtubechannel_oauth",
            ),
        ]
        return custom + urls

    def start_oauth(self, request, object_id):
        channel = YouTubeChannel.objects.get(pk=object_id)
        try:
            auth_url = youtube_service.build_authorization_url(channel)
        except Exception as exc:
            self.message_user(request, f"Falha ao iniciar OAuth: {exc}", level=messages.ERROR)
            return redirect("admin:channels_youtubechannel_change", channel.pk)
        return redirect(auth_url)
