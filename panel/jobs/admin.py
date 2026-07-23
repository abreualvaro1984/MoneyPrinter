from __future__ import annotations

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html

from panel.publishing.models import PublishTarget

from .models import Job
from . import worker


class PublishTargetInline(admin.TabularInline):
    model = PublishTarget
    extra = 1
    autocomplete_fields = ("account",)
    fields = (
        "account",
        "status",
        "title",
        "description",
        "tags",
        "hashtags",
        "privacy",
        "made_for_kids",
        "remote_id",
        "remote_url",
    )
    readonly_fields = ("remote_id", "remote_url")
    show_change_link = True


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "job_type",
        "niche",
        "subject_short",
        "status",
        "progress",
        "created_at",
        "actions_column",
    )
    list_filter = ("job_type", "status", "niche")
    search_fields = ("subject", "source_url", "output_title", "public_id", "error")
    inlines = [PublishTargetInline]
    readonly_fields = (
        "public_id",
        "engine_task_id",
        "work_dir",
        "output_video",
        "youtube_video_id",
        "progress",
        "log",
        "error",
        "result_json",
        "started_at",
        "finished_at",
        "published_at",
        "created_at",
        "updated_at",
    )
    actions = ["enqueue_jobs", "approve_and_upload"]

    @admin.display(description="Tema")
    def subject_short(self, obj: Job) -> str:
        text = obj.subject or obj.source_url or "-"
        return text[:60]

    @admin.display(description="Ações")
    def actions_column(self, obj: Job) -> str:
        bits = []
        if obj.status in {Job.Status.DRAFT, Job.Status.FAILED}:
            bits.append(
                format_html(
                    '<a class="button" href="{}">Enfileirar</a>',
                    reverse("admin:jobs_job_enqueue", args=[obj.pk]),
                )
            )
        if obj.status == Job.Status.AWAITING_REVIEW:
            bits.append(
                format_html(
                    '<a class="button" href="{}">Aprovar + Upload</a>',
                    reverse("admin:jobs_job_approve", args=[obj.pk]),
                )
            )
        return format_html(" ".join(bits)) if bits else "-"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/enqueue/",
                self.admin_site.admin_view(self.enqueue_view),
                name="jobs_job_enqueue",
            ),
            path(
                "<path:object_id>/approve/",
                self.admin_site.admin_view(self.approve_view),
                name="jobs_job_approve",
            ),
        ]
        return custom + urls

    def enqueue_view(self, request, object_id):
        job = Job.objects.get(pk=object_id)
        job.status = Job.Status.QUEUED
        job.error = ""
        job.save(update_fields=["status", "error", "updated_at"])
        job.append_log("Enfileirado via admin")
        try:
            worker.process_job(job.pk)
            self.message_user(request, f"Job #{job.pk} processado.", level=messages.SUCCESS)
        except Exception as exc:
            self.message_user(request, f"Falha: {exc}", level=messages.ERROR)
        return redirect("admin:jobs_job_change", job.pk)

    def approve_view(self, request, object_id):
        job = Job.objects.get(pk=object_id)
        try:
            worker.upload_job(job.pk)
            self.message_user(request, f"Upload iniciado/concluído para job #{job.pk}.", level=messages.SUCCESS)
        except Exception as exc:
            self.message_user(request, f"Falha no upload: {exc}", level=messages.ERROR)
        return redirect("admin:jobs_job_change", job.pk)

    @admin.action(description="Enfileirar jobs selecionados")
    def enqueue_jobs(self, request, queryset):
        for job in queryset:
            job.status = Job.Status.QUEUED
            job.save(update_fields=["status", "updated_at"])
            try:
                worker.process_job(job.pk)
            except Exception as exc:
                job.mark_failed(str(exc))
        self.message_user(request, "Jobs processados.")

    @admin.action(description="Aprovar e publicar (destinos / YouTube)")
    def approve_and_upload(self, request, queryset):
        for job in queryset.filter(status=Job.Status.AWAITING_REVIEW):
            try:
                worker.upload_job(job.pk)
            except Exception as exc:
                job.mark_failed(str(exc))
        self.message_user(request, "Uploads processados.")
