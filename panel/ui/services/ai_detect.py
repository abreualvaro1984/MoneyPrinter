from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Flash: rápido, barato e com bom limite no AI Studio (RPM generoso).
_DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
_MAX_CHARS = 24_000


@dataclass
class ScoreResult:
    score: float | None
    status: str
    raw: dict = field(default_factory=dict)


def score_text(text: str) -> ScoreResult:
    """
    Retorna score 0–100 (maior = mais parecido com IA) e status ScriptDraft.AiStatus.

    Ordem: Gemini (Google AI Studio) → GPTZero (opcional) → heurística local.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return ScoreResult(None, "unknown", {"error": "texto vazio"})

    gemini_key, gemini_source = _resolve_gemini_api_key()
    if gemini_key:
        try:
            result = _score_gemini(cleaned, gemini_key)
            result.raw["key_source"] = gemini_source
            return result
        except Exception as exc:
            logger.warning("Gemini anti-IA falhou, tentando fallback: %s", exc)
            gemini_error = str(exc)
    else:
        gemini_error = None

    gptzero_key = (
        getattr(settings, "GPTZERO_API_KEY", "")
        or os.environ.get("GPTZERO_API_KEY", "")
    ).strip()
    if gptzero_key:
        try:
            result = _score_gptzero(cleaned, gptzero_key)
            if gemini_error:
                result.raw["gemini_error"] = gemini_error
            return result
        except Exception as exc:
            logger.warning("GPTZero falhou, usando heurística: %s", exc)
            heuristic = _score_heuristic(cleaned)
            heuristic.raw["gptzero_error"] = str(exc)
            if gemini_error:
                heuristic.raw["gemini_error"] = gemini_error
            return heuristic

    result = _score_heuristic(cleaned)
    result.status = "skipped"
    notes = []
    if not gemini_key:
        notes.append(
            "Sem Gemini: cadastre em /apis/ (provider Gemini) ou GEMINI_API_KEY no panel/.env"
        )
    if gemini_error:
        notes.append(f"Gemini erro: {gemini_error}")
    notes.append("Usando heurística local")
    result.raw["note"] = " | ".join(notes)
    return result


def _resolve_gemini_api_key() -> tuple[str, str]:
    """Key do AI Studio: env ou credencial Gemini cadastrada no painel."""
    env_key = (
        getattr(settings, "GEMINI_API_KEY", "")
        or os.environ.get("GEMINI_API_KEY", "")
        or os.environ.get("GOOGLE_AI_STUDIO_API_KEY", "")
    ).strip()
    if env_key:
        return env_key, "env:GEMINI_API_KEY"

    try:
        from panel.ui.models import LlmCredential

        cred = (
            LlmCredential.objects.filter(provider="gemini", is_active=True)
            .order_by("-is_default", "-updated_at")
            .first()
        )
        if cred and (cred.api_key or "").strip():
            return cred.api_key.strip(), f"panel:gemini:{cred.pk}"
    except Exception as exc:
        logger.debug("não foi possível ler LlmCredential Gemini: %s", exc)
    return "", ""


def _gemini_model() -> str:
    return (
        getattr(settings, "GEMINI_DETECT_MODEL", "")
        or os.environ.get("GEMINI_DETECT_MODEL", "")
        or _DEFAULT_GEMINI_MODEL
    ).strip()


def _score_gemini(text: str, api_key: str) -> ScoreResult:
    sample = text[:_MAX_CHARS]
    model = _gemini_model()
    prompt = f"""You are an AI-writing detector for Brazilian Portuguese video scripts.
Score how likely the text was written by an LLM (not a human).

Return ONLY JSON:
{{
  "score": <number 0-100>,
  "label": "human" | "mixed" | "ai",
  "reasons": ["short reason 1", "short reason 2"]
}}

Rules for score:
- 0–40: clearly human / natural spoken PT-BR
- 41–69: mixed / needs review
- 70–100: strongly LLM-like (generic filler, rigid structure, stock phrases)

Text to evaluate:
\"\"\"
{sample}
\"\"\"
"""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    response = requests.post(
        url,
        params={"key": api_key},
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        },
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Gemini HTTP {response.status_code}: {response.text[:300]}")
    data = response.json()
    raw_text = _extract_gemini_text(data)
    parsed = _parse_json_object(raw_text)
    score = parsed.get("score")
    try:
        score_f = float(score) if score is not None else None
    except (TypeError, ValueError):
        score_f = None
    if score_f is not None:
        score_f = max(0.0, min(100.0, score_f))
    return ScoreResult(
        score_f,
        _status_from_score(score_f),
        {
            "provider": "gemini",
            "model": model,
            "label": parsed.get("label"),
            "reasons": parsed.get("reasons") or [],
            "response": data,
        },
    )


def _extract_gemini_text(data: dict) -> str:
    try:
        parts = data["candidates"][0]["content"]["parts"]
        chunks = [str(p.get("text") or "") for p in parts if isinstance(p, dict)]
        return "\n".join(c for c in chunks if c).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Resposta Gemini inesperada: {data!r}") from exc


def _parse_json_object(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return {}
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else {}


def _score_gptzero(text: str, api_key: str) -> ScoreResult:
    response = requests.post(
        "https://api.gptzero.me/v2/predict/text",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": api_key,
        },
        json={"document": text},
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    docs = data.get("documents") or []
    prob = None
    if docs:
        prob = docs[0].get("completely_generated_prob")
        if prob is None:
            prob = docs[0].get("average_generated_prob")
    score = float(prob) * 100 if prob is not None else None
    return ScoreResult(score, _status_from_score(score), {"provider": "gptzero", "response": data})


def _score_heuristic(text: str) -> ScoreResult:
    """Heurística barata: padrões típicos de LLM elevam o score."""
    lower = text.lower()
    hits = 0
    patterns = [
        r"neste vídeo",
        r"é importante ressaltar",
        r"em conclusão",
        r"além disso,",
        r"no entanto,",
        r"vamos explorar",
        r"sem mais delongas",
        r"em suma,",
        r"vale ressaltar",
        r"primeiro.*?segundo.*?terceiro",
    ]
    for pat in patterns:
        if re.search(pat, lower, re.S):
            hits += 1
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    if sentences:
        avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
        if avg_len > 28:
            hits += 1
        if len(sentences) >= 4:
            lengths = [len(s.split()) for s in sentences]
            if max(lengths) - min(lengths) < 4:
                hits += 1
    score = min(95.0, 15.0 + hits * 12.0)
    return ScoreResult(score, _status_from_score(score), {"provider": "heuristic", "hits": hits})


def _status_from_score(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 70:
        return "regen"
    if score >= 45:
        return "review"
    return "pass"
