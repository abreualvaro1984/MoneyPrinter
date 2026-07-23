from __future__ import annotations

import json
import logging
import re

from django.utils.text import slugify

from panel.jobs.engine_path import ensure_repo_on_path
from panel.niches.models import Niche
from panel.ui.models import LlmCredential, NicheDiscoveryRun
from panel.ui.services.llm_runtime import use_llm_credential

logger = logging.getLogger(__name__)


def discover_root_niches(
    *,
    llm_credential: LlmCredential | None = None,
    market: str = "Brasil",
) -> NicheDiscoveryRun:
    ensure_repo_on_path()
    prompt = f"""
Você é estrategista de conteúdo digital no {market} (YouTube Shorts, TikTok, Reels, Kwai).
Liste os nichos PRINCIPAIS mais promissores agora para um criador solo (monetização por views / brand deals).

Responda SOMENTE JSON:
{{
  "summary_pt": "visão geral em 2-4 frases",
  "niches": [
    {{
      "name": "nome curto do nicho",
      "why": "por que é forte agora",
      "keywords": ["kw1", "kw2", "kw3"],
      "heat_score": 0
    }}
  ]
}}
Gere entre 8 e 12 nichos. heat_score 0-100. Não invente métricas falsas de views.
""".strip()
    with use_llm_credential(llm_credential):
        data = _call_llm_json(prompt)
    niches = _normalize_suggestions(data.get("niches") or [])
    return NicheDiscoveryRun.objects.create(
        kind=NicheDiscoveryRun.Kind.ROOT,
        parent_niche=None,
        llm_credential=llm_credential,
        summary_pt=str(data.get("summary_pt") or ""),
        suggestions_json=niches,
    )


def discover_subniches(
    parent: Niche,
    *,
    llm_credential: LlmCredential | None = None,
) -> NicheDiscoveryRun:
    ensure_repo_on_path()
    prompt = f"""
Você é estrategista de conteúdo no Brasil.
Nicho pai já escolhido: {parent.name}
Briefing atual: {parent.briefing or "n/a"}
Keywords atuais: {", ".join(parent.keyword_list()) or "n/a"}

Sugira SUBNICHOS específicos (ângulos mais estreitos) para esse nicho, bons para shorts.

Responda SOMENTE JSON:
{{
  "summary_pt": "como fatiar o nicho",
  "niches": [
    {{
      "name": "nome do subnicho",
      "why": "por que atacar",
      "keywords": ["kw1", "kw2"],
      "heat_score": 0
    }}
  ]
}}
Gere entre 6 e 10 subnichos.
""".strip()
    with use_llm_credential(llm_credential):
        data = _call_llm_json(prompt)
    niches = _normalize_suggestions(data.get("niches") or [])
    return NicheDiscoveryRun.objects.create(
        kind=NicheDiscoveryRun.Kind.SUB,
        parent_niche=parent,
        llm_credential=llm_credential,
        summary_pt=str(data.get("summary_pt") or ""),
        suggestions_json=niches,
    )


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
            # nome único global — acrescenta sufixo
            name = f"{name} ({n})"

    # unique name constraint
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


def _normalize_suggestions(raw: list) -> list[dict]:
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
        out.append(
            {
                "name": name[:120],
                "why": str(item.get("why") or "")[:500],
                "keywords": [str(k)[:80] for k in kws[:8]],
                "heat_score": heat,
            }
        )
    out.sort(key=lambda x: -(x.get("heat_score") or 0))
    return out


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
            "summary_pt": f"Falha parcial na IA ({exc}). Sugestões genéricas abaixo.",
            "niches": [
                {
                    "name": "Finanças pessoais",
                    "why": "Alta demanda recorrente no BR",
                    "keywords": ["reserva de emergência", "investimentos"],
                    "heat_score": 70,
                },
                {
                    "name": "Saúde e bem-estar",
                    "why": "Evergreen com boa retenção",
                    "keywords": ["habitos", "sono", "treino"],
                    "heat_score": 65,
                },
                {
                    "name": "Curiosidades científicas",
                    "why": "Bom para shorts de alto alcance",
                    "keywords": ["ciencia", "espaco", "corpo humano"],
                    "heat_score": 68,
                },
            ],
        }
