from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from panel.channels import youtube as youtube_service
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

    # Preferência: Destinos de publicação (multi-conta / multi-plataforma)
    targets = list(job.publish_targets.select_related("account").all())
    if targets:
        from panel.publishing import service as publish_service
        from panel.publishing.models import PublishTarget

        job.status = Job.Status.UPLOADING
        job.save(update_fields=["status", "updated_at"])
        job.append_log(f"Publicando em {len(targets)} destino(s)")

        results = publish_service.publish_job_targets(job_id)
        published = [t for t in results if t.status == PublishTarget.Status.PUBLISHED]
        failed = [t for t in results if t.status == PublishTarget.Status.FAILED]

        yt = next(
            (t for t in published if t.account.platform == "youtube" and t.remote_id),
            None,
        )
        if yt:
            job.youtube_video_id = yt.remote_id

        if published and not failed:
            job.status = Job.Status.PUBLISHED
            job.published_at = timezone.now()
            job.append_log(
                "Publicado: " + ", ".join(t.remote_url or t.remote_id or str(t.account) for t in published)
            )
            job.save()
            return {"published": len(published), "failed": 0, "targets": [t.pk for t in published]}

        if published and failed:
            job.status = Job.Status.PUBLISHED
            job.published_at = timezone.now()
            job.error = "; ".join(f"{t.account}: {t.error}" for t in failed)
            job.append_log(f"Parcial: {len(published)} ok, {len(failed)} falha(s)")
            job.save()
            return {
                "published": len(published),
                "failed": len(failed),
                "errors": [t.error for t in failed],
            }

        err = "; ".join(t.error or "falha" for t in failed) or "Nenhum destino publicado"
        job.mark_failed(err)
        raise RuntimeError(err)

    # Fallback legado: canal YouTube OneToOne do nicho
    channel = job.channel
    if channel is None:
        channel = getattr(job.niche, "youtube_channel", None)
    if channel is None or not channel.is_ready:
        raise RuntimeError(
            f"Job #{job_id} sem Destinos de publicação e nicho '{job.niche}' "
            "sem canal YouTube conectado. Cadastre Contas sociais + Destinos, "
            "ou conecte o Canal YouTube legado."
        )

    job.status = Job.Status.UPLOADING
    job.save(update_fields=["status", "updated_at"])
    job.append_log(f"Upload legado para canal {channel}")

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
