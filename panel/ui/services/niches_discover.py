from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from django.utils.text import slugify

from panel.jobs.engine_path import ensure_repo_on_path
from panel.niches.models import Niche
from panel.ui.models import LlmCredential, NicheDiscoveryRun
from panel.ui.services.llm_runtime import use_llm_credential
from panel.ui.services.video_formats import (
    DEFAULT_VIDEO_FORMAT,
    get_video_format,
)

logger = logging.getLogger(__name__)

# Seeds genéricos (usados no formato "any" e como base complementar).
_MARKET_SEED_QUERIES = (
    "shorts brasil",
    "curiosidades brasil",
    "finanças pessoais",
    "investimentos brasil",
    "saúde bem estar",
    "treino em casa",
    "receitas rápidas",
    "tecnologia brasil",
    "história curiosidades",
    "relacionamentos dicas",
    "carreira emprego",
    "humor brasileiro",
)

# Categorias YouTube úteis para diversificar o chart BR.
_TRENDING_CATEGORY_IDS = (
    None,  # geral
    "22",  # People & Blogs
    "26",  # Howto & Style
    "27",  # Education
    "28",  # Science & Technology
    "24",  # Entertainment
)


def discover_root_niches(
    *,
    llm_credential: LlmCredential | None = None,
    market: str = "Brasil",
    video_format: str = DEFAULT_VIDEO_FORMAT,
) -> NicheDiscoveryRun:
    ensure_repo_on_path()
    fmt = get_video_format(video_format)
    signals = gather_hot_market_signals(video_format=fmt.id)
    evidence = _compact_evidence(signals.get("videos") or [])
    status = signals.get("status") or "unknown"
    errors = signals.get("errors") or []

    prompt = f"""
Você é estrategista de conteúdo digital no {market} (YouTube Shorts, TikTok, Reels, Kwai).

FORMATO ESCOLHIDO PELO CRIADOR: {fmt.label}
{fmt.short}

REGRAS DE FORMATO (obrigatórias):
{fmt.llm_rules}

TAREFA: extrair NICHOS em alta AGORA a partir das EVIDÊNCIAS REAIS abaixo
(vídeos do chart mostPopular e buscas recentes ordenadas por views).
NÃO invente nichos só porque “costumam funcionar”. Priorize o que os dados mostram.
DESCARTE evidências incompatíveis com o formato (ex.: vlog facial se o formato for dark).

Status da coleta: {status}
Erros (se houver): {json.dumps(errors, ensure_ascii=False)}

EVIDÊNCIAS (título, canal, views, fonte):
{json.dumps(evidence, ensure_ascii=False)}

Regras gerais:
1) Cada nicho sugerido DEVE citar ao menos 1 evidência (título + views) em "evidence".
2) heat_score 0-100 proporcional ao volume/recência das evidências (não chute).
3) Se status != "ok", diga isso no summary_pt e seja conservador.
4) keywords = termos de busca reais derivados dos títulos/temas.
5) format_ok=false se o nicho NÃO for produzível no formato escolhido — nesses casos NÃO inclua na lista.
6) format_fit 0-100 = quão bem o nicho casa com o formato.
7) format_notes = 1 frase: como produzir neste formato + por que a evidência valida.

Responda SOMENTE JSON:
{{
  "summary_pt": "o que está quente AGORA no formato {fmt.id} (2-4 frases)",
  "niches": [
    {{
      "name": "nome curto do nicho",
      "why": "por que está em alta AGORA (cite views/títulos)",
      "keywords": ["kw1", "kw2", "kw3"],
      "heat_score": 0,
      "format_ok": true,
      "format_fit": 0,
      "format_notes": "como produzir sem quebrar o formato + evidência",
      "evidence": [
        {{"title": "...", "view_count": 0, "url": "...", "source": "youtube_trending|youtube_search"}}
      ]
    }}
  ]
}}
Gere entre 8 e 12 nichos — todos com format_ok=true.
""".strip()

    with use_llm_credential(llm_credential):
        data = _call_llm_json(prompt)
    niches = _normalize_suggestions(
        data.get("niches") or [],
        evidence_fallback=evidence,
        video_format=fmt.id,
    )
    niches = _enrich_heat_from_evidence(niches)
    niches = _filter_by_format(niches, fmt.id)
    summary = str(data.get("summary_pt") or "").strip()
    if status != "ok" and not summary:
        summary = (
            "Coleta parcial de sinais do YouTube. "
            "Sugestões podem misturar evidências limitadas com inferência da IA."
        )
    if summary and fmt.id != "any":
        summary = f"[{fmt.label}] {summary}"
    return NicheDiscoveryRun.objects.create(
        kind=NicheDiscoveryRun.Kind.ROOT,
        parent_niche=None,
        llm_credential=llm_credential,
        summary_pt=summary,
        suggestions_json=niches,
        signals_json=signals,
        video_format=fmt.id,
    )


def discover_subniches(
    parent: Niche,
    *,
    llm_credential: LlmCredential | None = None,
    video_format: str = DEFAULT_VIDEO_FORMAT,
) -> NicheDiscoveryRun:
    ensure_repo_on_path()
    fmt = get_video_format(video_format)
    signals = gather_niche_signals(parent, video_format=fmt.id)
    evidence = _compact_evidence(signals.get("videos") or [])
    status = signals.get("status") or "unknown"
    errors = signals.get("errors") or []

    prompt = f"""
Você é estrategista de conteúdo no Brasil.
Nicho pai: {parent.name}
Briefing: {parent.briefing or "n/a"}
Keywords: {", ".join(parent.keyword_list()) or "n/a"}

FORMATO ESCOLHIDO: {fmt.label} — {fmt.short}
{fmt.llm_rules}

Com base nas EVIDÊNCIAS REAIS (vídeos recentes com mais views neste tema),
sugira SUBNICHOS específicos compatíveis com o formato. Não invente ângulos sem lastro.

Status da coleta: {status}
Erros: {json.dumps(errors, ensure_ascii=False)}

EVIDÊNCIAS:
{json.dumps(evidence, ensure_ascii=False)}

Responda SOMENTE JSON:
{{
  "summary_pt": "como o nicho está performando agora no formato {fmt.id}",
  "niches": [
    {{
      "name": "nome do subnicho",
      "why": "por que atacar agora (cite evidências)",
      "keywords": ["kw1", "kw2"],
      "heat_score": 0,
      "format_ok": true,
      "format_fit": 0,
      "format_notes": "como produzir neste formato",
      "evidence": [
        {{"title": "...", "view_count": 0, "url": "...", "source": "youtube_search"}}
      ]
    }}
  ]
}}
Gere entre 6 e 10 subnichos com format_ok=true.
""".strip()

    with use_llm_credential(llm_credential):
        data = _call_llm_json(prompt)
    niches = _normalize_suggestions(
        data.get("niches") or [],
        evidence_fallback=evidence,
        video_format=fmt.id,
    )
    niches = _enrich_heat_from_evidence(niches)
    niches = _filter_by_format(niches, fmt.id)
    summary = str(data.get("summary_pt") or "")
    if summary and fmt.id != "any":
        summary = f"[{fmt.label}] {summary}"
    return NicheDiscoveryRun.objects.create(
        kind=NicheDiscoveryRun.Kind.SUB,
        parent_niche=parent,
        llm_credential=llm_credential,
        summary_pt=summary,
        suggestions_json=niches,
        signals_json=signals,
        video_format=fmt.id,
    )


def gather_hot_market_signals(
    *,
    region_code: str = "BR",
    video_format: str = DEFAULT_VIDEO_FORMAT,
) -> dict:
    """Coleta o que está em alta agora no YouTube BR (chart + buscas recentes)."""
    from panel.channels import youtube as youtube_service

    fmt = get_video_format(video_format)
    videos: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=14)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    for category_id in _TRENDING_CATEGORY_IDS:
        try:
            batch = youtube_service.list_trending_videos(
                region_code=region_code,
                max_results=12 if category_id else 20,
                category_id=category_id,
            )
            for item in batch:
                vid = item.get("video_id")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                videos.append(item)
        except Exception as exc:
            label = category_id or "all"
            errors.append(f"trending[{label}]: {exc}")
            logger.warning("trending fetch failed category=%s: %s", category_id, exc)

    seed_queries = _seed_queries_for_format(fmt.id)
    for query in seed_queries:
        try:
            batch = youtube_service.search_videos(
                query,
                max_results=5,
                order="viewCount",
                published_after=published_after,
                region_code=region_code,
            )
            for item in batch:
                vid = item.get("video_id")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                videos.append(item)
        except Exception as exc:
            errors.append(f"search[{query}]: {exc}")
            logger.warning("seed search failed query=%s: %s", query, exc)

    videos.sort(key=lambda v: int(v.get("view_count") or 0), reverse=True)
    videos = videos[:60]
    if videos:
        status = "ok" if not errors else "partial"
    else:
        status = "empty"
    return {
        "status": status,
        "region": region_code,
        "video_format": fmt.id,
        "video_format_label": fmt.label,
        "seed_queries": list(seed_queries),
        "published_after": published_after,
        "errors": errors[:12],
        "videos": [_public_video(v) for v in videos],
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


def gather_niche_signals(
    parent: Niche,
    *,
    region_code: str = "BR",
    video_format: str = DEFAULT_VIDEO_FORMAT,
) -> dict:
    """Sinais reais em torno de um nicho já escolhido (para subnichos)."""
    from panel.channels import youtube as youtube_service

    fmt = get_video_format(video_format)
    queries = parent.keyword_list()[:6] or [parent.name]
    format_suffix = {
        "dark": "narrado shorts",
        "sleep": "para dormir",
        "blackscreen": "tela preta",
        "ambient": "ambience",
        "face": "falando shorts",
        "hybrid": "shorts",
        "screen": "tutorial shorts",
        "any": "shorts",
    }.get(fmt.id, "shorts")
    queries = list(
        dict.fromkeys(
            [
                *queries,
                parent.name,
                f"{parent.name} {format_suffix}",
                *fmt.seed_queries[:3],
            ]
        )
    )
    videos: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=21)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    for query in queries[:8]:
        try:
            batch = youtube_service.search_videos(
                query,
                max_results=6,
                order="viewCount",
                published_after=published_after,
                region_code=region_code,
            )
            for item in batch:
                vid = item.get("video_id")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                videos.append(item)
        except Exception as exc:
            errors.append(f"search[{query}]: {exc}")
            logger.warning("niche signal search failed query=%s: %s", query, exc)

    videos.sort(key=lambda v: int(v.get("view_count") or 0), reverse=True)
    videos = videos[:40]
    status = "ok" if videos and not errors else ("partial" if videos else "empty")
    return {
        "status": status,
        "region": region_code,
        "parent": parent.name,
        "video_format": fmt.id,
        "video_format_label": fmt.label,
        "queries": queries[:8],
        "published_after": published_after,
        "errors": errors[:12],
        "videos": [_public_video(v) for v in videos],
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


def _seed_queries_for_format(format_id: str) -> tuple[str, ...]:
    fmt = get_video_format(format_id)
    if fmt.id == "any":
        return _MARKET_SEED_QUERIES
    combined = list(fmt.seed_queries) + list(_MARKET_SEED_QUERIES[:6])
    return tuple(dict.fromkeys(combined))


def add_suggestion_as_niche(
    *,
    name: str,
    why: str = "",
    keywords: list[str] | None = None,
    parent: Niche | None = None,
) -> Niche:
    name = (name or "").strip()[:120]
    if not name:
        raise ValueError("Nome vazio")
    base_slug = slugify(name) or "nicho"
    slug = base_slug
    n = 2
    while Niche.objects.filter(slug=slug).exists():
        if Niche.objects.filter(name=name, parent=parent).exists():
            return Niche.objects.get(name=name, parent=parent)
        slug = f"{base_slug}-{n}"
        n += 1
        if Niche.objects.filter(name=name).exists() and parent is None:
            name = f"{name} ({n})"

    final_name = name
    if Niche.objects.filter(name=final_name).exists():
        suffix = 2
        while Niche.objects.filter(name=f"{name} {suffix}").exists():
            suffix += 1
        final_name = f"{name} {suffix}"

    kw_lines = "\n".join(k for k in (keywords or []) if k)
    briefing = why.strip()
    return Niche.objects.create(
        name=final_name[:120],
        slug=slug[:140],
        parent=parent,
        briefing=briefing,
        keywords=kw_lines,
        default_voice="pt-BR-FranciscaNeural-Female",
        default_language="pt-BR",
    )


def _public_video(item: dict) -> dict:
    return {
        "video_id": item.get("video_id") or "",
        "title": str(item.get("title") or "")[:180],
        "channel_title": str(item.get("channel_title") or "")[:120],
        "url": item.get("url") or "",
        "view_count": int(item.get("view_count") or 0),
        "published_at": item.get("published_at") or "",
        "query": item.get("query") or "",
        "source": item.get("source") or "youtube",
        "category_id": item.get("category_id") or "",
    }


def _compact_evidence(videos: list[dict], limit: int = 35) -> list[dict]:
    out = []
    for v in videos[:limit]:
        out.append(
            {
                "title": v.get("title"),
                "channel": v.get("channel_title"),
                "view_count": int(v.get("view_count") or 0),
                "url": v.get("url"),
                "source": v.get("source"),
                "query": v.get("query"),
            }
        )
    return out


def _normalize_suggestions(
    raw: list,
    *,
    evidence_fallback: list[dict] | None = None,
    video_format: str = DEFAULT_VIDEO_FORMAT,
) -> list[dict]:
    out = []
    for item in raw[:15]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        kws = item.get("keywords") or []
        if isinstance(kws, str):
            kws = [k.strip() for k in kws.split(",") if k.strip()]
        try:
            heat = max(0, min(100, int(item.get("heat_score") or 0)))
        except (TypeError, ValueError):
            heat = 0
        try:
            format_fit = max(0, min(100, int(item.get("format_fit") or 0)))
        except (TypeError, ValueError):
            format_fit = 0
        format_ok = item.get("format_ok")
        if format_ok is None:
            format_ok = True
        else:
            format_ok = bool(format_ok)
        evidence = _normalize_evidence(item.get("evidence") or [])
        if not evidence and evidence_fallback:
            evidence = [
                {
                    "title": e.get("title") or "",
                    "view_count": int(e.get("view_count") or 0),
                    "url": e.get("url") or "",
                    "source": e.get("source") or "youtube",
                }
                for e in evidence_fallback[:2]
            ]
        out.append(
            {
                "name": name[:120],
                "why": str(item.get("why") or "")[:500],
                "keywords": [str(k)[:80] for k in kws[:8]],
                "heat_score": heat,
                "format_ok": format_ok,
                "format_fit": format_fit,
                "format_notes": str(item.get("format_notes") or "")[:300],
                "video_format": video_format,
                "evidence": evidence,
            }
        )
    out.sort(key=lambda x: (-(x.get("format_fit") or 0), -(x.get("heat_score") or 0)))
    return out


def _filter_by_format(niches: list[dict], format_id: str) -> list[dict]:
    """Remove format_ok=false e aplica heurística leve de termos preferidos/evitados."""
    fmt = get_video_format(format_id)
    if fmt.id == "any":
        return niches

    kept = []
    for niche in niches:
        if niche.get("format_ok") is False:
            continue
        blob = " ".join(
            [
                str(niche.get("name") or ""),
                str(niche.get("why") or ""),
                " ".join(niche.get("keywords") or []),
                " ".join(
                    str(e.get("title") or "") for e in (niche.get("evidence") or [])
                ),
            ]
        ).lower()

        if fmt.avoid_terms and any(t in blob for t in fmt.avoid_terms):
            fit = int(niche.get("format_fit") or 0)
            if fit < 55:
                continue
            niche["format_fit"] = min(fit, 50)
            note = niche.get("format_notes") or ""
            niche["format_notes"] = (
                f"{note} · aviso: termos típicos de outro formato".strip(" ·")
            )

        if fmt.prefer_terms and any(t in blob for t in fmt.prefer_terms):
            niche["format_fit"] = max(int(niche.get("format_fit") or 0), 60)

        if int(niche.get("format_fit") or 0) == 0:
            niche["format_fit"] = 55

        kept.append(niche)

    kept.sort(key=lambda x: (-(x.get("format_fit") or 0), -(x.get("heat_score") or 0)))
    return kept


def _normalize_evidence(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:5]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        try:
            views = max(0, int(item.get("view_count") or 0))
        except (TypeError, ValueError):
            views = 0
        out.append(
            {
                "title": title[:180],
                "view_count": views,
                "url": str(item.get("url") or "")[:300],
                "source": str(item.get("source") or "youtube")[:40],
            }
        )
    return out


def _enrich_heat_from_evidence(niches: list[dict]) -> list[dict]:
    """Ajusta heat_score com base em views reais citadas (quando existirem)."""
    for niche in niches:
        views = [
            int(e.get("view_count") or 0)
            for e in (niche.get("evidence") or [])
            if int(e.get("view_count") or 0) > 0
        ]
        if not views:
            continue
        top = max(views)
        derived = min(95, 25 + (top.bit_length() * 4))
        current = int(niche.get("heat_score") or 0)
        niche["heat_score"] = max(current, derived) if current else derived
    niches.sort(key=lambda x: -(x.get("heat_score") or 0))
    return niches


def _call_llm_json(prompt: str) -> dict:
    try:
        from app.services import llm

        raw = llm._generate_response(prompt)
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0) if match else raw)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.exception("niche discovery LLM failed")
        return {
            "summary_pt": (
                f"Falha parcial na IA ({exc}). "
                "Use as evidências YouTube na tela; sugestões genéricas abaixo são só fallback."
            ),
            "niches": [
                {
                    "name": "Finanças pessoais",
                    "why": "Fallback sem evidência vinculada — cadastre YouTube API key em /apis/.",
                    "keywords": ["reserva de emergência", "investimentos"],
                    "heat_score": 40,
                    "format_ok": True,
                    "format_fit": 50,
                    "format_notes": "Fallback genérico",
                    "evidence": [],
                },
                {
                    "name": "Saúde e bem-estar",
                    "why": "Fallback sem evidência vinculada — cadastre YouTube API key em /apis/.",
                    "keywords": ["habitos", "sono", "treino"],
                    "heat_score": 35,
                    "format_ok": True,
                    "format_fit": 50,
                    "format_notes": "Fallback genérico",
                    "evidence": [],
                },
            ],
        }
