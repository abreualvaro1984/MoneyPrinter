from __future__ import annotations

from django.contrib import admin

from panel.ui.models import LlmCredential, ScriptDraft, TrendRun


@admin.register(LlmCredential)
class LlmCredentialAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "model_name", "is_default", "is_active", "updated_at")
    list_filter = ("provider", "is_active", "is_default")
    search_fields = ("name", "provider", "model_name", "notes")
    readonly_fields = ("created_at", "updated_at")


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
