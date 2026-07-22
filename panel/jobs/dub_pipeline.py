from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from django.conf import settings

from panel.jobs.clip_pipeline import download_youtube, transcribe_video
from panel.jobs.engine_path import ensure_repo_on_path
from panel.jobs.models import Job


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr}"
        )


def translate_segments_to_pt(segments: list[dict], niche_briefing: str = "") -> list[dict]:
    ensure_repo_on_path()
    from app.services import llm

    payload = [{"i": i, "text": seg["text"]} for i, seg in enumerate(segments)]
    prompt = f"""
Traduza o conteúdo a seguir para português do Brasil natural, falado em voz de narração.
Briefing: {niche_briefing or "geral"}
Mantenha o mesmo número de itens e o campo i.
Responda SOMENTE JSON:
[{{"i": 0, "text": "..."}}]

Entrada:
{json.dumps(payload, ensure_ascii=False)}
""".strip()
    raw = llm._generate_response(prompt)
    match = re.search(r"\[.*\]", raw, re.S)
    data = json.loads(match.group(0) if match else raw)
    by_i = {int(item["i"]): item["text"] for item in data}
    out = []
    for i, seg in enumerate(segments):
        out.append({**seg, "text_pt": by_i.get(i, seg["text"])})
    return out


def synthesize_dub_audio(segments_pt: list[dict], work_dir: Path, voice_name: str) -> Path:
    ensure_repo_on_path()
    from app.services import voice

    full_text = " ".join(seg["text_pt"] for seg in segments_pt if seg.get("text_pt"))
    if not full_text.strip():
        raise RuntimeError("Texto PT vazio para dublagem.")
    audio_file = work_dir / "dub_pt.mp3"
    sub_maker = voice.tts(
        text=full_text,
        voice_name=voice_name,
        voice_rate=1.0,
        voice_file=str(audio_file),
        voice_volume=1.0,
    )
    if sub_maker is None and not audio_file.exists():
        raise RuntimeError("TTS falhou ao gerar áudio PT.")
    return audio_file


def mux_replace_audio(video_path: Path, audio_path: Path, dest: Path) -> Path:
    ensure_repo_on_path()
    from app.utils import utils

    # Shortest stream wins so dub length mismatch doesn't hang forever.
    _run(
        [
            utils.get_ffmpeg_binary(),
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )
    return dest


def run_dub_job(job: Job) -> str:
    if not job.source_url:
        raise ValueError("Dub exige source_url.")

    work_dir = job.ensure_work_dir()
    voice_name = job.niche.default_voice or settings.PANEL_DEFAULT_VOICE

    job.append_log(f"Baixando fonte {job.source_url}")
    source = download_youtube(job.source_url, work_dir)

    job.append_log("Transcrevendo áudio original...")
    segments = transcribe_video(source, work_dir)
    # Limit length for MVP cost/time
    segments = segments[:200]
    (work_dir / "dub_segments_en.json").write_text(
        json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    job.append_log("Traduzindo para PT-BR...")
    segments_pt = translate_segments_to_pt(segments, job.niche.briefing)
    (work_dir / "dub_segments_pt.json").write_text(
        json.dumps(segments_pt, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    job.append_log(f"Gerando TTS ({voice_name})...")
    audio = synthesize_dub_audio(segments_pt, work_dir, voice_name)

    dest = work_dir / "dub-pt.mp4"
    job.append_log("Muxando vídeo + áudio PT...")
    mux_replace_audio(source, audio, dest)

    if not job.output_title:
        job.output_title = (job.subject or f"{job.niche.name} (PT)")[:100]
        job.save(update_fields=["output_title", "updated_at"])

    job.append_log(f"Dub OK → {dest}")
    return str(dest)
