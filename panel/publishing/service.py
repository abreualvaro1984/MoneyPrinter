from __future__ import annotations

from pathlib import Path

from panel.publishing.connectors import connector_for_account
from panel.publishing.models import PublishTarget, SocialAccount


def publish_target(target: PublishTarget, video_path: str | None = None) -> PublishTarget:
    job = target.job
    path = video_path or job.output_video
    if not path or not Path(path).is_file():
        target.mark_failed(f"Vídeo inválido: {path}")
        return target

    account: SocialAccount = target.account
    if not account.is_ready:
        target.mark_failed(f"Conta não pronta: {account}")
        return target

    connector = connector_for_account(account)
    target.status = PublishTarget.Status.UPLOADING
    target.save(update_fields=["status", "updated_at"])

    metadata = target.to_metadata()
    # Fill sensible defaults from job if empty
    if not metadata.get("title"):
        metadata["title"] = job.output_title or job.subject or account.name
    if not metadata.get("description") and not metadata.get("caption"):
        metadata["description"] = job.output_description or ""
        metadata["caption"] = metadata["description"] or metadata["title"]

    result = connector.upload(account, path, metadata)
    if result.success:
        target.mark_published(remote_id=result.remote_id, remote_url=result.remote_url)
        # stash raw in extra_json
        extra = dict(target.extra_json or {})
        extra["last_publish_raw"] = result.raw
        target.extra_json = extra
        target.save(update_fields=["extra_json", "updated_at"])
    else:
        target.mark_failed(result.error or "Falha no upload")
    return target


def publish_job_targets(job_id: int) -> list[PublishTarget]:
    targets = list(
        PublishTarget.objects.filter(job_id=job_id)
        .exclude(status=PublishTarget.Status.PUBLISHED)
        .select_related("account", "job")
    )
    for target in targets:
        if target.status in {
            PublishTarget.Status.PENDING,
            PublishTarget.Status.READY,
            PublishTarget.Status.FAILED,
        }:
            publish_target(target)
    return list(PublishTarget.objects.filter(job_id=job_id).select_related("account"))
