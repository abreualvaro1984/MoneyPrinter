from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass
class ScoreResult:
    score: float | None
    status: str
    raw: dict = field(default_factory=dict)


def score_text(text: str) -> ScoreResult:
    """
    Retorna score 0–100 (maior = mais parecido com IA) e status ScriptDraft.AiStatus.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return ScoreResult(None, "unknown", {"error": "texto vazio"})

    api_key = (
        getattr(settings, "GPTZERO_API_KEY", "")
        or os.environ.get("GPTZERO_API_KEY", "")
    ).strip()
    if api_key:
        try:
            return _score_gptzero(cleaned, api_key)
        except Exception as exc:
            logger.warning("GPTZero falhou, usando heurística: %s", exc)
            heuristic = _score_heuristic(cleaned)
            heuristic.raw["gptzero_error"] = str(exc)
            return heuristic

    result = _score_heuristic(cleaned)
    result.status = "skipped"
    result.raw["note"] = "GPTZERO_API_KEY não configurada; heurística local apenas"
    return result


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
    # documents[0].completely_generated_prob is 0–1
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
    # Frases muito longas e uniformes
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
