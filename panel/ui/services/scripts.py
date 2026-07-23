from __future__ import annotations

import json
import logging
import re

from panel.jobs.engine_path import ensure_repo_on_path
from panel.niches.models import Niche
from panel.ui.models import ScriptDraft, TrendRun
from panel.ui.services import ai_detect

logger = logging.getLogger(__name__)


def generate_script(
    niche: Niche,
    topic: str,
    *,
    trend_run: TrendRun | None = None,
    anti_detect: bool = False,
) -> ScriptDraft:
    ensure_repo_on_path()
    data = _llm_script(niche, topic, anti_detect=anti_detect)
    draft = ScriptDraft.objects.create(
        niche=niche,
        trend_run=trend_run,
        topic=topic,
        title=str(data.get("title") or topic)[:200],
        body=str(data.get("body") or ""),
        hooks=str(data.get("hooks") or ""),
        cta=str(data.get("cta") or ""),
        hashtags=str(data.get("hashtags") or "")[:500],
        version=1,
    )
    score_result = ai_detect.score_text(draft.body)
    draft.mark_scored(
        score_result.score,
        score_result.status,
        score_result.raw,
    )
    return draft


def regenerate_script(draft: ScriptDraft) -> ScriptDraft:
    """Cria nova versão com prompt anti-detecção."""
    new = generate_script(
        draft.niche,
        draft.topic,
        trend_run=draft.trend_run,
        anti_detect=True,
    )
    new.version = draft.version + 1
    new.notes = f"Regenerado a partir do draft #{draft.pk}"
    new.save(update_fields=["version", "notes", "updated_at"])
    return new


def rescore(draft: ScriptDraft) -> ScriptDraft:
    result = ai_detect.score_text(draft.body)
    draft.mark_scored(result.score, result.status, result.raw)
    return draft


def _llm_script(niche: Niche, topic: str, *, anti_detect: bool) -> dict:
    try:
        from app.services import llm

        anti = ""
        if anti_detect:
            anti = """
Modo anti-detecção: escreva como criador BR falando no celular.
Use contrações, frases curtas e longas misturadas, uma imperfeição leve,
evite listas simétricas e aberturas de LLM ("Neste vídeo vamos...", "É importante ressaltar").
"""
        paragraphs = niche.paragraph_number or 1
        prompt = f"""
Você escreve roteiros falados para shorts/reels no Brasil.
Nicho: {niche.name}
Briefing: {niche.briefing or "n/a"}
Tema: {topic}
Idioma: {niche.default_language or "pt-BR"}
Alvo aproximado: {paragraphs} bloco(s) narrados (short).
{anti}

Responda SOMENTE JSON:
{{
  "title": "título curto",
  "body": "roteiro completo para narração",
  "hooks": "2-3 aberturas alternativas",
  "cta": "chamada para ação",
  "hashtags": "#tag1 #tag2"
}}
""".strip()
        raw = llm._generate_response(prompt)
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0) if match else raw)
        if not isinstance(data, dict):
            raise ValueError("LLM não retornou objeto")
        return data
    except Exception as exc:
        logger.exception("script LLM failed")
        return {
            "title": topic[:100],
            "body": (
                f"Olha, sobre {topic}… "
                f"Vou te falar do jeito direto, sem enrolação. "
                f"(Roteiro fallback — LLM indisponível: {exc})"
            ),
            "hooks": f"Você já reparou isso sobre {topic}?",
            "cta": "Comenta se quer a parte 2.",
            "hashtags": f"#{niche.slug} #shorts",
        }
