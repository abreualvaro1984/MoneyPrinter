from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from panel.channels import youtube as youtube_service
from panel.channels.models import YouTubeChannel
from panel.jobs.models import Job

logger = logging.getLogger(__name__)


def process_job(job_id: int) -> None:
    with transaction.atomic():
        job = Job.objects.select_for_update().select_related("niche", "channel").get(pk=job_id)
        if job.status not in {Job.Status.QUEUED, Job.Status.DRAFT, Job.Status.FAILED}:
            if job.status == Job.Status.RUNNING:
                raise RuntimeError(f"Job #{job_id} já está running.")
            # Allow re-process from awaiting_review only via explicit recreate
        job.mark_running()

    job = Job.objects.select_related("niche", "channel").get(pk=job_id)
    try:
        if job.job_type == Job.JobType.CREATE:
            from panel.jobs.create_pipeline import run_create_job

            output = run_create_job(job)
        elif job.job_type == Job.JobType.CLIP:
            from panel.jobs.clip_pipeline import run_clip_job

            output = run_clip_job(job)
        elif job.job_type == Job.JobType.DUB:
            from panel.jobs.dub_pipeline import run_dub_job

            output = run_dub_job(job)
        elif job.job_type == Job.JobType.RESEARCH:
            from panel.research.service import run_research_job

            output = run_research_job(job)
            job.status = Job.Status.AWAITING_REVIEW
            job.progress = 100
            job.finished_at = timezone.now()
            job.result_json = output
            job.append_log("Pesquisa concluída")
            job.save()
            return
        else:
            raise ValueError(f"Tipo de job não suportado: {job.job_type}")

        job.mark_awaiting_review(output)
    except Exception as exc:
        logger.exception("job %s failed", job_id)
        job.mark_failed(f"{type(exc).__name__}: {exc}")
        raise


def upload_job(job_id: int) -> dict:
    job = Job.objects.select_related("niche", "channel").get(pk=job_id)
    if job.status not in {Job.Status.AWAITING_REVIEW, Job.Status.APPROVED}:
        raise RuntimeError(f"Job #{job_id} não está pronto para upload (status={job.status}).")
    if not job.output_video:
        raise RuntimeError("Job sem output_video.")

    channel = job.channel
    if channel is None:
        channel = getattr(job.niche, "youtube_channel", None)
    if channel is None or not channel.is_ready:
        raise RuntimeError(
            f"Nicho '{job.niche}' sem canal YouTube conectado. Conecte no admin."
        )

    job.status = Job.Status.UPLOADING
    job.save(update_fields=["status", "updated_at"])
    job.append_log(f"Upload para canal {channel}")

    title = job.output_title or job.subject or job.niche.name
    description = job.output_description or ""
    if "#shorts" not in description.lower() and job.niche.default_aspect == "9:16":
        description = f"{description}\n\n#shorts".strip()

    response = youtube_service.upload_video(
        channel,
        job.output_video,
        title=title,
        description=description,
        tags=job.niche.keyword_list()[:15],
        privacy_status=job.privacy_status or "private",
    )
    video_id = response.get("id", "")
    job.youtube_video_id = video_id
    job.status = Job.Status.PUBLISHED
    job.published_at = timezone.now()
    job.channel = channel
    job.append_log(f"Publicado: https://youtu.be/{video_id}")
    job.save()
    return response


def process_queued(limit: int = 5) -> int:
    ids = list(
        Job.objects.filter(status=Job.Status.QUEUED)
        .order_by("created_at")
        .values_list("id", flat=True)[:limit]
    )
    done = 0
    for job_id in ids:
        process_job(job_id)
        done += 1
    return done
