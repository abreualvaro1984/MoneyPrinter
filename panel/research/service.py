from __future__ import annotations

import json
import re

from panel.channels import youtube as youtube_service
from panel.jobs.engine_path import ensure_repo_on_path
from panel.jobs.models import Job
from panel.niches.models import Niche
from panel.research.models import ResearchSnapshot


def gather_candidates(niche: Niche, max_per_keyword: int = 5) -> list[dict]:
    keywords = niche.keyword_list() or [niche.name]
    seen = set()
    candidates: list[dict] = []
    for kw in keywords[:8]:
        try:
            items = youtube_service.search_videos(kw, max_results=max_per_keyword, order="viewCount")
        except Exception as exc:
            candidates.append({"error": str(exc), "query": kw})
            continue
        for item in items:
            vid = item["video_id"]
            if vid in seen:
                continue
            seen.add(vid)
            item["query"] = kw
            candidates.append(item)
    return candidates


def suggest_from_candidates(niche: Niche, candidates: list[dict]) -> dict:
    ensure_repo_on_path()
    from app.services import llm

    compact = [
        {
            "title": c.get("title"),
            "channel": c.get("channel_title"),
            "url": c.get("url"),
            "query": c.get("query"),
        }
        for c in candidates
        if c.get("url")
    ][:25]

    prompt = f"""
Você é estrategista de conteúdo YouTube BR.
Nicho: {niche.name}
Briefing: {niche.briefing or "n/a"}
Keywords: {", ".join(niche.keyword_list()) or niche.name}

Vídeos em alta / encontrados:
{json.dumps(compact, ensure_ascii=False)}

Responda SOMENTE JSON:
{{
  "summary_pt": "resumo do que está bombando",
  "create_topics": ["tema short 1", "tema 2", "tema 3"],
  "clip_targets": [{{"url": "...", "why": "...", "cut_topic": "..."}}]
}}
""".strip()
    raw = llm._generate_response(prompt)
    match = re.search(r"\{.*\}", raw, re.S)
    return json.loads(match.group(0) if match else raw)


def run_research_for_niche(niche: Niche) -> ResearchSnapshot:
    candidates = gather_candidates(niche)
    suggestions = {"summary_pt": "", "create_topics": [], "clip_targets": []}
    try:
        if any(c.get("url") for c in candidates):
            suggestions = suggest_from_candidates(niche, candidates)
    except Exception as exc:
        suggestions = {
            "summary_pt": f"Pesquisa parcial (LLM falhou: {exc})",
            "create_topics": [f"{niche.name}: ideia rápida 1", f"{niche.name}: ideia rápida 2"],
            "clip_targets": [
                {"url": c["url"], "why": "candidato por views", "cut_topic": niche.name}
                for c in candidates
                if c.get("url")
            ][:3],
        }

    snap = ResearchSnapshot.objects.create(
        niche=niche,
        query=", ".join(niche.keyword_list()[:5]) or niche.name,
        summary_pt=suggestions.get("summary_pt", ""),
        suggestions_json=suggestions,
        candidates_json=candidates,
    )
    return snap


def run_research_job(job: Job) -> dict:
    snap = run_research_for_niche(job.niche)
    job.append_log(f"Snapshot #{snap.pk} criado com {len(snap.candidates_json)} candidatos")
    return {
        "snapshot_id": snap.pk,
        "summary_pt": snap.summary_pt,
        "suggestions": snap.suggestions_json,
        "candidates": snap.candidates_json,
    }
