from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html

from panel.publishing.models import PublishTarget, SocialAccount
from panel.publishing import service as publish_service


@admin.register(SocialAccount)
class SocialAccountAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "platform",
        "username",
        "niche",
        "auth_mode",
        "status",
        "is_active",
        "is_ready_display",
        "updated_at",
    )
    list_filter = ("platform", "status", "auth_mode", "is_active", "niche")
    search_fields = ("name", "username", "external_id", "notes")
    readonly_fields = ("connected_at", "created_at", "updated_at", "platform_help")
    fieldsets = (
        (
            "Conta",
            {
                "fields": (
                    "name",
                    "platform",
                    "niche",
                    "username",
                    "external_id",
                    "auth_mode",
                    "status",
                    "is_active",
                    "default_privacy",
                    "platform_help",
                )
            },
        ),
        (
            "Credenciais",
            {
                "fields": ("credentials_json", "notes", "last_error"),
                "description": (
                    "YouTube OAuth JSON (token/refresh_token/...). "
                    "Upload-Post: {\"api_key\": \"...\", \"username\": \"...\"}. "
                    "Meta/Facebook: {\"access_token\": \"...\", \"page_id\": \"...\"}."
                ),
            },
        ),
        ("Auditoria", {"fields": ("connected_at", "created_at", "updated_at")}),
    )

    @admin.display(boolean=True, description="Pronta")
    def is_ready_display(self, obj: SocialAccount) -> bool:
        return obj.is_ready

    @admin.display(description="Campos / monetização")
    def platform_help(self, obj: SocialAccount) -> str:
        if not obj or not obj.platform:
            return "-"
        from panel.publishing.catalog import get_platform

        try:
            spec = get_platform(obj.platform)
        except KeyError:
            return "-"
        fields = "<br>".join(
            f"{'*' if f.required else '-'} <b>{f.label}</b> (<code>{f.key}</code>): {f.help_text}"
            for f in spec.fields
        )
        return format_html(
            "<p><b>{}</b></p><p>{}</p><p>{}</p>",
            spec.name,
            spec.monetization_notes,
            fields,
        )

    def save_model(self, request, obj: SocialAccount, form, change):
        if obj.credentials_json and obj.status == SocialAccount.Status.DRAFT:
            obj.status = SocialAccount.Status.CONNECTED
            if not obj.connected_at:
                from django.utils import timezone

                obj.connected_at = timezone.now()
        super().save_model(request, obj, form, change)


@admin.register(PublishTarget)
class PublishTargetAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "job",
        "account",
        "status",
        "title_short",
        "privacy",
        "remote_id",
        "updated_at",
    )
    list_filter = ("status", "account__platform", "account")
    search_fields = ("title", "description", "remote_id", "job__subject", "account__name")
    autocomplete_fields = ("job", "account")
    readonly_fields = ("remote_id", "remote_url", "error", "published_at", "created_at", "updated_at")
    actions = ["mark_ready", "publish_now"]

    @admin.display(description="Título")
    def title_short(self, obj: PublishTarget) -> str:
        return (obj.title or "-")[:50]

    @admin.action(description="Marcar como pronto")
    def mark_ready(self, request, queryset):
        updated = queryset.update(status=PublishTarget.Status.READY)
        self.message_user(request, f"{updated} destino(s) marcados como prontos.")

    @admin.action(description="Publicar agora nos conectores")
    def publish_now(self, request, queryset):
        ok = 0
        fail = 0
        for target in queryset.select_related("account", "job"):
            publish_service.publish_target(target)
            target.refresh_from_db()
            if target.status == PublishTarget.Status.PUBLISHED:
                ok += 1
            else:
                fail += 1
        self.message_user(
            request,
            f"Publicados: {ok}. Falhas: {fail}.",
            level=messages.SUCCESS if fail == 0 else messages.WARNING,
        )


class PlatformCatalogAdmin(admin.ModelAdmin):
    """Read-only virtual listing via a simple proxy page — use the doc instead."""


# Expose platform catalog as an admin view without a model: soft note in SocialAccount.
