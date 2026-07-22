from __future__ import annotations

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html

from panel.jobs.models import Job
from panel.niches.models import Niche

from .models import ResearchSnapshot
from . import service


@admin.register(ResearchSnapshot)
class ResearchSnapshotAdmin(admin.ModelAdmin):
    list_display = ("id", "niche", "query", "created_at", "spawn_actions")
    list_filter = ("niche",)
    readonly_fields = (
        "niche",
        "query",
        "summary_pt",
        "suggestions_json",
        "candidates_json",
        "created_at",
    )

    @admin.display(description="Criar jobs")
    def spawn_actions(self, obj: ResearchSnapshot) -> str:
        return format_html(
            '<a class="button" href="{}">Gerar jobs Create/Clip</a>',
            reverse("admin:research_researchsnapshot_spawn", args=[obj.pk]),
        )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/spawn/",
                self.admin_site.admin_view(self.spawn_view),
                name="research_researchsnapshot_spawn",
            ),
            path(
                "run/<int:niche_id>/",
                self.admin_site.admin_view(self.run_niche_view),
                name="research_run_niche",
            ),
        ]
        return custom + urls

    def spawn_view(self, request, object_id):
        snap = ResearchSnapshot.objects.select_related("niche").get(pk=object_id)
        suggestions = snap.suggestions_json or {}
        created = 0
        for topic in (suggestions.get("create_topics") or [])[:5]:
            Job.objects.create(
                niche=snap.niche,
                channel=getattr(snap.niche, "youtube_channel", None),
                job_type=Job.JobType.CREATE,
                subject=str(topic)[:300],
                status=Job.Status.DRAFT,
                output_title=str(topic)[:100],
            )
            created += 1
        for target in (suggestions.get("clip_targets") or [])[:5]:
            Job.objects.create(
                niche=snap.niche,
                channel=getattr(snap.niche, "youtube_channel", None),
                job_type=Job.JobType.CLIP,
                subject=str(target.get("cut_topic") or snap.niche.name)[:300],
                source_url=target.get("url") or "",
                cut_topic=str(target.get("cut_topic") or "")[:300],
                status=Job.Status.DRAFT,
                output_description=str(target.get("why") or ""),
            )
            created += 1
        self.message_user(request, f"{created} jobs criados em rascunho.", level=messages.SUCCESS)
        return redirect("admin:jobs_job_changelist")

    def run_niche_view(self, request, niche_id):
        niche = Niche.objects.get(pk=niche_id)
        try:
            snap = service.run_research_for_niche(niche)
            self.message_user(request, f"Pesquisa OK — snapshot #{snap.pk}", level=messages.SUCCESS)
            return redirect("admin:research_researchsnapshot_change", snap.pk)
        except Exception as exc:
            self.message_user(request, f"Falha: {exc}", level=messages.ERROR)
            return redirect("admin:research_researchsnapshot_changelist")
