from __future__ import annotations

from django.contrib import admin

from panel.ui.forms import LlmCredentialForm
from panel.ui.models import LlmCredential, NicheDiscoveryRun, ScriptDraft, TrendRun


@admin.register(LlmCredential)
class LlmCredentialAdmin(admin.ModelAdmin):
    """Só IA + API key. Nome, URL e modelo são preenchidos automaticamente."""

    form = LlmCredentialForm
    change_form_template = "admin/ui/llmcredential/change_form.html"
    list_display = ("name", "provider", "model_name", "is_default", "is_active", "updated_at")
    list_filter = ("provider", "is_active", "is_default")
    search_fields = ("name", "provider", "model_name")
    readonly_fields = ("name", "base_url", "model_name", "created_at", "updated_at")
    fields = (
        "provider",
        "api_key",
        "is_default",
        "name",
        "base_url",
        "model_name",
        "created_at",
        "updated_at",
    )

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("provider", "api_key", "is_default")
        return self.fields


@admin.register(NicheDiscoveryRun)
class NicheDiscoveryRunAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "video_format", "parent_niche", "created_at")
    list_filter = ("kind", "video_format")
    readonly_fields = ("created_at",)


@admin.register(TrendRun)
class TrendRunAdmin(admin.ModelAdmin):
    list_display = ("id", "niche", "llm_credential", "created_at")
    list_filter = ("niche",)
    readonly_fields = ("created_at",)


@admin.register(ScriptDraft)
class ScriptDraftAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "niche", "ai_status", "ai_score", "version", "updated_at")
    list_filter = ("ai_status", "niche")
    search_fields = ("title", "topic", "body")
    readonly_fields = ("created_at", "updated_at", "ai_raw")
