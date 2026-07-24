from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from django.conf import settings

from panel.jobs.engine_path import ensure_repo_on_path
from panel.niches.models import Niche
from panel.ui.models import LlmCredential, ScriptDraft, VideoPlan
from panel.ui.services.llm_runtime import use_llm_credential
from panel.ui.services.video_formats import (
    DEFAULT_VIDEO_FORMAT,
    get_video_format,
)

logger = logging.getLogger(__name__)

_EDGE_VOICES_PT = (
    "pt-BR-FranciscaNeural-Female",
    "pt-BR-AntonioNeural-Male",
    "pt-BR-BrendaNeural-Female",
    "pt-BR-DonatoNeural-Male",
    "pt-BR-ElzaNeural-Female",
    "pt-BR-FabioNeural-Male",
    "pt-BR-GiovannaNeural-Female",
    "pt-BR-HumbertoNeural-Male",
    "pt-BR-JulioNeural-Male",
    "pt-BR-LeilaNeural-Female",
    "pt-BR-LeticiaNeural-Female",
    "pt-BR-ManuelaNeural-Female",
    "pt-BR-NicolauNeural-Male",
    "pt-BR-ThalitaNeural-Female",
    "pt-BR-ValerioNeural-Male",
    "pt-BR-YaraNeural-Female",
)

_FACELESS_FORMATS = frozenset({"dark", "sleep", "blackscreen", "ambient"})


def create_plan(
    *,
    niche: Niche,
    topic: str = "",
    video_format: str = DEFAULT_VIDEO_FORMAT,
    llm_credential: LlmCredential | None = None,
) -> VideoPlan:
    ensure_repo_on_path()
    fmt = get_video_format(video_format)
    topic = (topic or "").strip() or f"Vídeo para {niche.name}"
    evidence = _gather_plan_signals(niche, fmt.id)
    data = _call_plan_llm(niche, topic, fmt.id, evidence, llm_credential=llm_credential)
    voice = _pick_voice(niche, data.get("voice_name"))
    assets = _normalize_assets(data.get("assets") or [], fmt.id)
    dubs = _normalize_dubs(data.get("dub_suggestions") or [], evidence)
    plan = VideoPlan.objects.create(
        niche=niche,
        llm_credential=llm_credential,
        video_format=fmt.id,
        topic=topic[:300],
        title=str(data.get("title") or topic)[:200],
        script_body=str(data.get("script_body") or data.get("body") or ""),
        voice_name=voice,
        voice_notes=str(data.get("voice_notes") or "")[:1000],
        assets_json=assets,
        dub_suggestions_json=dubs,
        plan_json={
            "summary_pt": str(data.get("summary_pt") or ""),
            "beats": data.get("beats") or [],
            "cta": str(data.get("cta") or ""),
            "hashtags": str(data.get("hashtags") or ""),
            "evidence": evidence,
            "raw_keys": sorted(data.keys()),
        },
        status=VideoPlan.Status.READY,
    )
    return plan


def regenerate_plan(plan: VideoPlan) -> VideoPlan:
    new = create_plan(
        niche=plan.niche,
        topic=plan.topic or plan.title,
        video_format=plan.video_format or DEFAULT_VIDEO_FORMAT,
        llm_credential=plan.llm_credential,
    )
    new.plan_json = {
        **(new.plan_json or {}),
        "regenerated_from": plan.pk,
    }
    new.save(update_fields=["plan_json", "updated_at"])
    return new


def export_to_script_draft(plan: VideoPlan) -> ScriptDraft:
    """Cria ScriptDraft a partir do roteiro do plano (área Roteiros)."""
    draft = ScriptDraft.objects.create(
        niche=plan.niche,
        topic=plan.topic or plan.title or plan.niche.name,
        title=(plan.title or plan.topic or "")[:200],
        body=plan.script_body or "",
        hooks="",
        cta=str((plan.plan_json or {}).get("cta") or ""),
        hashtags=str((plan.plan_json or {}).get("hashtags") or "")[:500],
        version=1,
        notes=f"Exportado do Plano #{plan.pk}",
    )
    plan.script_draft = draft
    plan.save(update_fields=["script_draft", "updated_at"])
    return draft


def _gather_plan_signals(niche: Niche, format_id: str) -> dict:
    """Buscas leves no YouTube para evidência / candidatos a dub."""
    from panel.channels import youtube as youtube_service

    videos: list[dict] = []
    dub_candidates: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=30)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    kw = niche.keyword_list()[:4] or [niche.name]
    local_queries = [f"{q} shorts" for q in kw[:3]]
    dub_queries = [
        f"{niche.name} explained",
        f"{kw[0]} documentary" if kw else f"{niche.name} english",
        f"{niche.name} podcast clip",
    ]

    for query in local_queries[:3]:
        try:
            batch = youtube_service.search_videos(
                query,
                max_results=4,
                order="viewCount",
                published_after=published_after,
                region_code="BR",
            )
            for item in batch:
                vid = item.get("video_id")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                videos.append(
                    {
                        "title": item.get("title"),
                        "url": item.get("url"),
                        "channel": item.get("channel_title"),
                        "view_count": int(item.get("view_count") or 0),
                        "query": query,
                    }
                )
        except Exception as exc:
            errors.append(f"search[{query}]: {exc}")
            logger.warning("plan signal search failed: %s", exc)

    for query in dub_queries[:3]:
        try:
            batch = youtube_service.search_videos(
                query,
                max_results=3,
                order="viewCount",
                published_after=published_after,
                region_code="US",
            )
            for item in batch:
                vid = item.get("video_id")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                dub_candidates.append(
                    {
                        "title": item.get("title"),
                        "url": item.get("url"),
                        "channel": item.get("channel_title"),
                        "view_count": int(item.get("view_count") or 0),
                        "query": query,
                        "language": "en",
                    }
                )
        except Exception as exc:
            errors.append(f"dub[{query}]: {exc}")
            logger.warning("plan dub search failed: %s", exc)

    return {
        "format": format_id,
        "videos": videos[:12],
        "dub_candidates": dub_candidates[:8],
        "errors": errors[:8],
    }


def _call_plan_llm(
    niche: Niche,
    topic: str,
    format_id: str,
    evidence: dict,
    *,
    llm_credential: LlmCredential | None,
) -> dict:
    fmt = get_video_format(format_id)
    faceless = format_id in _FACELESS_FORMATS
    asset_rule = (
        "Assets: priorize stock_image, stock_video, blackscreen, broll — SEM 'recorded' "
        "(criador não aparece)."
        if faceless
        else "Assets: misture 'recorded' (takes na câmera) com broll/stock quando fizer sentido. "
        "Não sugira só tela preta."
    )
    voices = ", ".join(_EDGE_VOICES_PT[:8])
    prompt = f"""
Você é diretor de conteúdo para Shorts/Reels no Brasil.
Monte um PLANO DE VÍDEO completo (não renderize; só planeje).

Nicho: {niche.name}
Briefing: {niche.briefing or "n/a"}
Keywords: {", ".join(niche.keyword_list()) or "n/a"}
Tema: {topic}
Formato: {fmt.label} — {fmt.short}
Regras do formato:
{fmt.llm_rules}

{asset_rule}
Vozes Edge TTS permitidas (escolha uma): {voices}
Voz padrão do nicho: {niche.default_voice or settings.PANEL_DEFAULT_VOICE}

Evidências YouTube (use se úteis; não invente URLs):
{json.dumps(evidence, ensure_ascii=False)[:6000]}

Responda SOMENTE JSON:
{{
  "title": "título curto",
  "summary_pt": "resumo do plano em 2 frases",
  "script_body": "roteiro falado completo em pt-BR",
  "beats": ["gancho", "desenvolvimento", "cta"],
  "cta": "chamada final",
  "hashtags": "#a #b",
  "voice_name": "pt-BR-....Neural-...",
  "voice_notes": "por que essa voz",
  "assets": [
    {{
      "kind": "stock_image|stock_video|recorded|blackscreen|broll",
      "query_or_brief": "o que buscar ou gravar",
      "why": "por que",
      "timing_hint": "início|meio|fim"
    }}
  ],
  "dub_suggestions": [
    {{
      "title": "vídeo gringo para dublar",
      "url": "https://... ou vazio",
      "channel": "",
      "why": "por que dublar",
      "language": "en",
      "search_query": "query se url vazia"
    }}
  ]
}}
Gere 4–8 assets e 3–5 dub_suggestions.
Roteiro conversacional, humano, sem clichês de LLM.
""".strip()

    with use_llm_credential(llm_credential):
        try:
            from app.services import llm

            raw = llm._generate_response(prompt)
            match = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(match.group(0) if match else raw)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.exception("video plan LLM failed")
            return _fallback_plan(niche, topic, format_id, str(exc))


def _fallback_plan(niche: Niche, topic: str, format_id: str, err: str) -> dict:
    faceless = format_id in _FACELESS_FORMATS
    assets = (
        [
            {
                "kind": "blackscreen" if format_id == "blackscreen" else "stock_video",
                "query_or_brief": niche.name,
                "why": "Fallback sem IA",
                "timing_hint": "meio",
            },
            {
                "kind": "broll",
                "query_or_brief": f"{niche.name} ambient",
                "why": "Cobertura visual",
                "timing_hint": "início",
            },
        ]
        if faceless
        else [
            {
                "kind": "recorded",
                "query_or_brief": "Take falando o gancho pra câmera",
                "why": "Presença do criador",
                "timing_hint": "início",
            },
            {
                "kind": "broll",
                "query_or_brief": niche.name,
                "why": "Cortes de apoio",
                "timing_hint": "meio",
            },
        ]
    )
    return {
        "title": topic[:120],
        "summary_pt": f"Plano fallback (IA falhou: {err[:120]}).",
        "script_body": (
            f"Fala sobre {topic}. "
            f"Use o tom do nicho {niche.name}. "
            "Abra com um gancho, explique o ponto e feche com um CTA simples."
        ),
        "beats": ["gancho", "ponto principal", "cta"],
        "cta": "Segue pra mais desse tema.",
        "hashtags": f"#{niche.slug}",
        "voice_name": niche.default_voice or settings.PANEL_DEFAULT_VOICE,
        "voice_notes": "Voz padrão do nicho (fallback).",
        "assets": assets,
        "dub_suggestions": [
            {
                "title": f"{niche.name} explained (EN)",
                "url": "",
                "channel": "",
                "why": "Candidato genérico a dublagem — busque no YouTube.",
                "language": "en",
                "search_query": f"{niche.name} explained",
            }
        ],
    }


def _pick_voice(niche: Niche, suggested: object) -> str:
    name = str(suggested or "").strip()
    if name in _EDGE_VOICES_PT:
        return name
    # Aceita variantes sem sufixo Female/Male
    for v in _EDGE_VOICES_PT:
        if name and name in v:
            return v
    return (
        (niche.default_voice or "").strip()
        or getattr(settings, "PANEL_DEFAULT_VOICE", "")
        or _EDGE_VOICES_PT[0]
    )


def _normalize_assets(raw: list, format_id: str) -> list[dict]:
    allowed = {
        "stock_image",
        "stock_video",
        "recorded",
        "blackscreen",
        "broll",
    }
    faceless = format_id in _FACELESS_FORMATS
    out = []
    for item in raw[:10]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "broll").strip().lower()
        if kind not in allowed:
            kind = "broll"
        if faceless and kind == "recorded":
            kind = "broll"
        brief = str(item.get("query_or_brief") or item.get("query") or "").strip()
        if not brief:
            continue
        out.append(
            {
                "kind": kind,
                "query_or_brief": brief[:240],
                "why": str(item.get("why") or "")[:300],
                "timing_hint": str(item.get("timing_hint") or "")[:40],
            }
        )
    return out


def _normalize_dubs(raw: list, evidence: dict) -> list[dict]:
    out = []
    candidates = evidence.get("dub_candidates") or []
    for i, item in enumerate(raw[:6]):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        url = str(item.get("url") or "").strip()
        if not url and i < len(candidates):
            url = str(candidates[i].get("url") or "")
            if not title or title.lower().startswith("vídeo"):
                title = str(candidates[i].get("title") or title)
        out.append(
            {
                "title": title[:200],
                "url": url[:400],
                "channel": str(item.get("channel") or "")[:120],
                "why": str(item.get("why") or "")[:300],
                "language": str(item.get("language") or "en")[:12],
                "search_query": str(item.get("search_query") or "")[:160],
            }
        )
    if not out and candidates:
        for c in candidates[:3]:
            out.append(
                {
                    "title": str(c.get("title") or "")[:200],
                    "url": str(c.get("url") or "")[:400],
                    "channel": str(c.get("channel") or "")[:120],
                    "why": "Candidato encontrado no YouTube (EN).",
                    "language": "en",
                    "search_query": str(c.get("query") or "")[:160],
                }
            )
    return out
