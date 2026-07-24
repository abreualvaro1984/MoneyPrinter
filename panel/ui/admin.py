from __future__ import annotations

from django.contrib import admin

from panel.ui.forms import LlmCredentialForm
from panel.ui.models import LlmCredential, NicheDiscoveryRun, ScriptDraft, TrendRun, VideoPlan


USAGE_HISTORY_LIMIT = 20


@admin.register(LlmCredential)
class LlmCredentialAdmin(admin.ModelAdmin):
    """IA + modelo + API key. Nome e URL base são preenchidos automaticamente."""

    form = LlmCredentialForm
    change_form_template = "admin/ui/llmcredential/change_form.html"
    list_display = ("name", "provider", "model_name", "is_default", "is_active", "updated_at")
    list_filter = ("provider", "is_active", "is_default")
    search_fields = ("name", "provider", "model_name")
    readonly_fields = ("name", "base_url", "created_at", "updated_at")
    fields = (
        "provider",
        "model_name",
        "api_key",
        "is_default",
        "name",
        "base_url",
        "created_at",
        "updated_at",
    )

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("provider", "model_name", "api_key", "is_default")
        return self.fields

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        obj = self.get_object(request, object_id)
        if obj is not None:
            extra_context.update(self._usage_history_context(obj))
        return super().change_view(
            request, object_id, form_url, extra_context=extra_context
        )

    def _usage_history_context(self, obj: LlmCredential) -> dict:
        trends_qs = obj.trend_runs.select_related("niche")
        niches_qs = obj.niche_discoveries.select_related("parent_niche")
        plans_qs = obj.video_plans.select_related("niche")
        return {
            "show_usage_history": True,
            "usage_summary": {
                "trends": trends_qs.count(),
                "niches": niches_qs.count(),
                "plans": plans_qs.count(),
            },
            "usage_trends": list(trends_qs[:USAGE_HISTORY_LIMIT]),
            "usage_niche_discoveries": list(niches_qs[:USAGE_HISTORY_LIMIT]),
            "usage_video_plans": list(plans_qs[:USAGE_HISTORY_LIMIT]),
        }


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
    list_display = ("id", "title", "niche", "llm_credential", "ai_status", "ai_score", "version", "updated_at")
    list_filter = ("ai_status", "niche", "llm_credential")
    search_fields = ("title", "topic", "body")
    readonly_fields = ("created_at", "updated_at", "ai_raw")
    raw_id_fields = ("llm_credential", "trend_run", "niche")


@admin.register(VideoPlan)
class VideoPlanAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "niche",
        "video_format",
        "status",
        "llm_credential",
        "updated_at",
    )
    list_filter = ("status", "video_format", "niche")
    search_fields = ("title", "topic", "script_body")
    readonly_fields = ("created_at", "updated_at", "plan_json")
