from __future__ import annotations

import json
import logging
import re

from panel.jobs.engine_path import ensure_repo_on_path
from panel.niches.models import Niche
from panel.research import service as research_service
from panel.ui.models import LlmCredential, TrendRun
from panel.ui.services.llm_runtime import use_llm_credential

logger = logging.getLogger(__name__)

HEURISTIC_PLATFORMS = {"tiktok", "instagram", "facebook", "kwai"}


def run_trends(
    niche: Niche,
    platforms: list[str],
    *,
    llm_credential: LlmCredential | None = None,
) -> TrendRun:
    platforms = [p for p in platforms if p] or ["youtube"]
    candidates: list[dict] = []
    platform_notes: list[dict] = []

    if "youtube" in platforms:
        try:
            yt = research_service.gather_candidates(niche)
            for item in yt:
                if item.get("url"):
                    row = dict(item)
                    row["platform"] = "youtube"
                    candidates.append(row)
                elif item.get("error"):
                    platform_notes.append(
                        {"platform": "youtube", "status": "error", "detail": item["error"]}
                    )
            # Prioriza vídeos com mais views
            candidates.sort(key=lambda c: int(c.get("view_count") or 0), reverse=True)
            if not any(c.get("platform") == "youtube" for c in candidates):
                platform_notes.append(
                    {
                        "platform": "youtube",
                        "status": "empty",
                        "detail": "Nenhum candidato YouTube (verifique YOUTUBE_API_KEY / OAuth).",
                    }
                )
        except Exception as exc:
            logger.exception("youtube trends failed")
            platform_notes.append(
                {"platform": "youtube", "status": "error", "detail": str(exc)}
            )

    for platform in platforms:
        if platform in HEURISTIC_PLATFORMS:
            platform_notes.append(
                {
                    "platform": platform,
                    "status": "heuristic",
                    "detail": (
                        "Discovery automático ainda sem API estável neste painel. "
                        "Temas usam briefing/keywords do nicho (sem métricas inventadas)."
                    ),
                }
            )

    with use_llm_credential(llm_credential):
        summary, topics = _consolidate(niche, platforms, candidates, platform_notes)

    return TrendRun.objects.create(
        niche=niche,
        platforms=platforms,
        summary_pt=summary,
        topics_json=topics,
        candidates_json=candidates[:40],
        llm_credential=llm_credential,
        error="",
    )


def _consolidate(
    niche: Niche,
    platforms: list[str],
    candidates: list[dict],
    platform_notes: list[dict],
) -> tuple[str, list[dict]]:
    ensure_repo_on_path()
    compact = [
        {
            "platform": c.get("platform", "youtube"),
            "title": c.get("title"),
            "channel": c.get("channel_title"),
            "url": c.get("url"),
            "query": c.get("query"),
            "view_count": int(c.get("view_count") or 0),
            "like_count": int(c.get("like_count") or 0),
            "published_at": c.get("published_at"),
        }
        for c in candidates
        if c.get("url")
    ][:25]

    try:
        from app.services import llm

        prompt = f"""
Você é estrategista de conteúdo short-form no Brasil.
Nicho: {niche.name}
Briefing: {niche.briefing or "n/a"}
Keywords: {", ".join(niche.keyword_list()) or niche.name}
Plataformas pedidas: {", ".join(platforms)}
Notas de coleta: {json.dumps(platform_notes, ensure_ascii=False)}
Vídeos mais quentes (ordenados por view_count quando disponível):
{json.dumps(compact, ensure_ascii=False)}

Missão:
1) Priorize assuntos que geram MAIS visualização (use view_count real; não invente números).
2) Para cada tema, diga se o criador deve ADICIONAR (produzir) ou PULAR.
3) recommendation = "add" ou "skip".
4) heat_score de 0 a 100 (potencial de views no nicho).

Responda SOMENTE JSON:
{{
  "summary_pt": "resumo do que está quente e o que vale atacar",
  "topics": [
    {{
      "title": "tema curto em PT",
      "why": "por que está quente / por que pular",
      "recommendation": "add|skip",
      "heat_score": 0,
      "platform": "youtube|tiktok|instagram|facebook|kwai",
      "source": "youtube|heuristic",
      "ref_url": "",
      "view_count": 0
    }}
  ]
}}
Gere entre 5 e 10 topics. Inclua alguns "skip" honestos se o ângulo for saturado ou fraco.
""".strip()
        raw = llm._generate_response(prompt)
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0) if match else raw)
        topics = data.get("topics") or []
        summary = data.get("summary_pt") or ""
        if not isinstance(topics, list):
            topics = []
        normalized = []
        for t in topics[:12]:
            if not isinstance(t, dict):
                continue
            rec = str(t.get("recommendation") or "add").lower().strip()
            if rec not in {"add", "skip"}:
                rec = "add"
            t["recommendation"] = rec
            try:
                t["heat_score"] = max(0, min(100, int(t.get("heat_score") or 0)))
            except (TypeError, ValueError):
                t["heat_score"] = 0
            try:
                t["view_count"] = int(t.get("view_count") or 0)
            except (TypeError, ValueError):
                t["view_count"] = 0
            normalized.append(t)
        # add primeiro, depois por heat
        normalized.sort(
            key=lambda x: (0 if x.get("recommendation") == "add" else 1, -(x.get("heat_score") or 0))
        )
        return summary, normalized
    except Exception as exc:
        logger.exception("trends LLM failed")
        fallback: list[dict] = []
        for c in compact[:5]:
            views = int(c.get("view_count") or 0)
            fallback.append(
                {
                    "title": c.get("title") or f"Tema {niche.name}",
                    "why": f"Alto volume de views no YouTube ({views:,}).".replace(",", "."),
                    "recommendation": "add" if views > 0 else "skip",
                    "heat_score": min(95, 40 + (views.bit_length() * 3) if views else 20),
                    "platform": "youtube",
                    "source": "youtube",
                    "ref_url": c.get("url") or "",
                    "view_count": views,
                }
            )
        for i, kw in enumerate((niche.keyword_list() or [niche.name])[:3], 1):
            fallback.append(
                {
                    "title": f"{kw}: ângulo rápido {i}",
                    "why": "Fallback local (LLM indisponível)",
                    "recommendation": "skip",
                    "heat_score": 25,
                    "platform": platforms[0] if platforms else "youtube",
                    "source": "heuristic",
                    "ref_url": "",
                    "view_count": 0,
                }
            )
        notes = "; ".join(n.get("detail", "") for n in platform_notes if n.get("detail"))
        return (f"Pesquisa parcial ({exc}). {notes}".strip(), fallback)
