from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings

from panel.jobs.engine_path import ensure_repo_on_path
from panel.jobs.models import Job


def run_create_job(job: Job) -> str:
    """Run MoneyPrinterTurbo create pipeline for a niche job. Returns output video path."""
    ensure_repo_on_path()

    from app.models.schema import VideoParams
    from app.services import task as tm
    from app.utils import utils

    niche = job.niche
    work_dir = job.ensure_work_dir()
    task_id = str(job.public_id)
    job.engine_task_id = task_id
    job.save(update_fields=["engine_task_id", "updated_at"])

    voice = niche.default_voice or settings.PANEL_DEFAULT_VOICE
    language = niche.default_language or settings.PANEL_DEFAULT_LANGUAGE
    aspect = niche.default_aspect or settings.PANEL_DEFAULT_ASPECT
    source = niche.default_video_source or settings.PANEL_DEFAULT_VIDEO_SOURCE

    params = VideoParams(
        video_subject=job.subject or niche.name,
        video_script=job.script_override or "",
        video_language=language,
        voice_name=voice,
        video_aspect=aspect,
        video_source=source,
        paragraph_number=max(1, min(10, niche.paragraph_number or 1)),
        video_clip_duration=3,
        subtitle_enabled=True,
        bgm_type="random",
        video_script_prompt=(
            f"Nicho: {niche.name}. {niche.briefing}".strip() if niche.briefing else ""
        ),
    )

    job.append_log(f"Create start task_id={task_id} subject={params.video_subject}")
    result = tm.start(task_id, params, stop_at="video")
    if result.get("state") == -1 or result.get("error"):
        raise RuntimeError(result.get("error") or "pipeline failed")

    engine_dir = Path(utils.task_dir(task_id))
    finals = sorted(engine_dir.glob("final-*.mp4"))
    if not finals:
        raise RuntimeError(f"Nenhum final-*.mp4 em {engine_dir}")

    dest = work_dir / finals[0].name
    shutil.copy2(finals[0], dest)

    script_src = engine_dir / "script.json"
    if script_src.exists():
        shutil.copy2(script_src, work_dir / "script.json")

    if not job.output_title:
        job.output_title = (job.subject or niche.name)[:100]
        job.save(update_fields=["output_title", "updated_at"])

    job.append_log(f"Create OK → {dest}")
    return str(dest)
