from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from panel.jobs.engine_path import ensure_repo_on_path
from panel.jobs.models import Job


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr}"
        )


def download_youtube(url: str, dest_dir: Path) -> Path:
    ensure_repo_on_path()
    import yt_dlp

    dest_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(dest_dir / "source.%(ext)s")
    opts = {
        "outtmpl": outtmpl,
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
        if path.suffix != ".mp4":
            mp4 = dest_dir / "source.mp4"
            if mp4.exists():
                return mp4
        # yt-dlp may finalize as source.mp4 after merge
        candidate = dest_dir / "source.mp4"
        if candidate.exists():
            return candidate
        if path.exists():
            return path
    raise RuntimeError(f"Download falhou para {url}")


def transcribe_video(video_path: Path, work_dir: Path) -> list[dict]:
    """Return list of {start, end, text} using faster-whisper via subtitle service audio extract."""
    ensure_repo_on_path()
    from app.services import subtitle
    from app.utils import utils

    audio_path = work_dir / "source_audio.mp3"
    _run(
        [
            utils.get_ffmpeg_binary(),
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "libmp3lame",
            "-q:a",
            "4",
            str(audio_path),
        ]
    )
    srt_path = work_dir / "source.srt"
    subtitle.create(str(audio_path), str(srt_path))
    return _parse_srt(srt_path)


def _parse_srt(path: Path) -> list[dict]:
    content = path.read_text(encoding="utf-8", errors="ignore")
    blocks = re.split(r"\n\s*\n", content.strip())
    segments = []
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        # find timing line
        timing = None
        text_lines = []
        for ln in lines:
            if "-->" in ln:
                timing = ln
            elif timing is not None and not ln.isdigit():
                text_lines.append(ln)
        if not timing:
            continue
        start_s, end_s = [p.strip() for p in timing.split("-->")]
        segments.append(
            {
                "start": _srt_ts_to_sec(start_s),
                "end": _srt_ts_to_sec(end_s),
                "text": " ".join(text_lines).strip(),
            }
        )
    return segments


def _srt_ts_to_sec(ts: str) -> float:
    ts = ts.replace(",", ".")
    hh, mm, rest = ts.split(":")
    ss = float(rest)
    return int(hh) * 3600 + int(mm) * 60 + ss


def propose_cuts(
    segments: list[dict],
    *,
    topic: str,
    target_duration: int,
    niche_briefing: str = "",
) -> list[dict]:
    ensure_repo_on_path()
    from app.services import llm

    transcript = "\n".join(
        f"[{seg['start']:.1f}-{seg['end']:.1f}] {seg['text']}" for seg in segments[:400]
    )
    prompt = f"""
Você é um editor de YouTube Shorts em português do Brasil.
Transcrição com timestamps (segundos):
{transcript}

Briefing do nicho: {niche_briefing or "geral"}
Assunto desejado do corte: {topic or "melhores momentos virais"}
Duração alvo de cada corte: cerca de {target_duration} segundos.

Proponha até 3 cortes. Responda SOMENTE JSON válido:
[
  {{"start": 12.5, "end": 52.0, "title": "...", "reason": "..."}}
]
""".strip()

    try:
        text = llm._generate_response(prompt)
    except Exception:
        text = None

    if not text:
        # Heuristic fallback: first continuous window near target duration
        if not segments:
            raise RuntimeError("Sem segmentos para cortar")
        start = segments[0]["start"]
        end = min(segments[-1]["end"], start + target_duration)
        return [
            {
                "start": start,
                "end": end,
                "title": topic or "Corte 1",
                "reason": "fallback sem LLM",
            }
        ]

    match = re.search(r"\[.*\]", text, re.S)
    raw = match.group(0) if match else text
    cuts = json.loads(raw)
    if not isinstance(cuts, list) or not cuts:
        raise RuntimeError(f"LLM não retornou cortes válidos: {text[:500]}")
    return cuts


def render_cut(video_path: Path, start: float, end: float, dest: Path) -> Path:
    ensure_repo_on_path()
    from app.utils import utils

    duration = max(0.5, end - start)
    _run(
        [
            utils.get_ffmpeg_binary(),
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(video_path),
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )
    return dest


def run_clip_job(job: Job) -> str:
    if not job.source_url:
        raise ValueError("Clip exige source_url (YouTube).")

    work_dir = job.ensure_work_dir()
    job.append_log(f"Baixando {job.source_url}")
    source = download_youtube(job.source_url, work_dir)
    job.append_log(f"Fonte: {source}")

    job.append_log("Transcrevendo com Whisper...")
    segments = transcribe_video(source, work_dir)
    (work_dir / "transcript_segments.json").write_text(
        json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    job.append_log("Pedindo cortes à IA...")
    cuts = propose_cuts(
        segments,
        topic=job.cut_topic or job.subject,
        target_duration=job.target_duration_sec or 45,
        niche_briefing=job.niche.briefing,
    )
    (work_dir / "proposed_cuts.json").write_text(
        json.dumps(cuts, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    best = cuts[0]
    dest = work_dir / "clip-1.mp4"
    job.append_log(
        f"Renderizando corte {best.get('start')}→{best.get('end')}: {best.get('title')}"
    )
    render_cut(source, float(best["start"]), float(best["end"]), dest)

    if not job.output_title:
        job.output_title = str(best.get("title") or job.subject or "Short")[:100]
    if not job.output_description:
        job.output_description = str(best.get("reason") or "")
    job.result_json = {**(job.result_json or {}), "cuts": cuts, "selected": best}
    job.save(
        update_fields=["output_title", "output_description", "result_json", "updated_at"]
    )
    job.append_log(f"Clip OK → {dest}")
    return str(dest)
