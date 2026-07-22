from __future__ import annotations

from django.contrib import admin

from .models import Niche


@admin.action(description="Pesquisar trends/YouTube destes nichos")
def run_research_action(modeladmin, request, queryset):
    from panel.research import service

    for niche in queryset:
        try:
            service.run_research_for_niche(niche)
        except Exception as exc:
            modeladmin.message_user(request, f"{niche}: {exc}", level=40)
    modeladmin.message_user(request, "Pesquisas disparadas.")


@admin.register(Niche)
class NicheAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "default_aspect",
        "default_language",
        "default_voice",
        "is_active",
        "updated_at",
    )
    list_filter = ("is_active", "default_aspect", "default_language")
    search_fields = ("name", "slug", "keywords", "briefing")
    prepopulated_fields = {"slug": ("name",)}
    actions = [run_research_action]