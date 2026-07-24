from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from panel.jobs.engine_path import ensure_repo_on_path
from panel.niches.models import Niche
from panel.ui.models import LlmCredential, ScriptDraft, TrendRun
from panel.ui.services import ai_detect
from panel.ui.services.llm_runtime import use_llm_credential

logger = logging.getLogger(__name__)

EVIDENCE_MAX_AGE_DAYS = 90
USER_AGENT = "MoneyPrinterPanel/1.0 (+script-research)"


def suggest_topics(
    niche: Niche,
    *,
    llm_credential: LlmCredential | None = None,
    count: int = 5,
) -> list[str]:
    """Sugere temas curtos e específicos para o nicho (lista curta para a UI)."""
    ensure_repo_on_path()
    count = max(3, min(8, int(count or 5)))
    seeds: list[str] = []
    recent = (
        TrendRun.objects.filter(niche=niche)
        .order_by("-created_at")
        .values_list("topics_json", flat=True)[:2]
    )
    for topics_json in recent:
        if not isinstance(topics_json, list):
            continue
        for row in topics_json:
            if not isinstance(row, dict):
                continue
            title = (row.get("title") or "").strip()
            if title and title not in seeds:
                seeds.append(title)
            if len(seeds) >= 8:
                break
        if len(seeds) >= 8:
            break

    try:
        from app.services import llm

        prompt = f"""
Você sugere temas de short/reel para criadores no Brasil.
Nicho: {niche.name}
Briefing: {niche.briefing or "n/a"}
Keywords: {", ".join(niche.keyword_list()) or niche.name}
Temas recentes do painel (só inspiração, pode variar): {json.dumps(seeds[:8], ensure_ascii=False)}

Devolva EXATAMENTE {count} temas:
- específicos (não genéricos), faláveis em short
- em pt-BR
- sem iniciais no lugar de nomes (proibido RDJ etc.)
- use nomes como o público BR chama pessoa/obra/aparelho

Responda SOMENTE JSON:
{{"topics": ["tema 1", "tema 2", "tema 3", "tema 4", "tema 5"]}}
""".strip()
        with use_llm_credential(llm_credential):
            raw = llm._generate_response(prompt)
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0) if match else raw)
        topics = data.get("topics") if isinstance(data, dict) else None
        if not isinstance(topics, list):
            raise ValueError("sem lista topics")
        cleaned = []
        for item in topics:
            text = str(item or "").strip()
            if text and text not in cleaned:
                cleaned.append(text[:300])
            if len(cleaned) >= count:
                break
        if cleaned:
            return cleaned[:count]
    except Exception:
        logger.exception("suggest_topics LLM failed")

    # Fallback local
    base = niche.keyword_list() or [niche.name]
    fallback = [
        f"{niche.name}: {kw} em 60s" if kw != niche.name else f"O que ninguém fala sobre {niche.name}"
        for kw in base[:count]
    ]
    while len(fallback) < count:
        fallback.append(f"{niche.name} — ideia {len(fallback) + 1}")
    return fallback[:count]


def generate_script(
    niche: Niche,
    topic: str,
    *,
    trend_run: TrendRun | None = None,
    llm_credential: LlmCredential | None = None,
    anti_detect: bool = False,
    target_duration_sec: int = 60,
) -> ScriptDraft:
    ensure_repo_on_path()
    target_duration_sec = _clamp_duration(target_duration_sec)
    evidence = gather_script_evidence(niche, topic, trend_run=trend_run)
    data = _llm_script(
        niche,
        topic,
        anti_detect=anti_detect,
        llm_credential=llm_credential,
        evidence=evidence,
        target_duration_sec=target_duration_sec,
    )
    notes_bits = []
    if evidence.get("videos"):
        notes_bits.append(f"{len(evidence['videos'])} vídeos (≤{EVIDENCE_MAX_AGE_DAYS}d)")
    if evidence.get("articles"):
        notes_bits.append(f"{len(evidence['articles'])} artigos/notícias")
    if evidence.get("errors"):
        notes_bits.append(f"avisos: {len(evidence['errors'])}")
    research_note = "Pesquisa: " + (", ".join(notes_bits) if notes_bits else "sem fontes externas")
    research_note += f" · alvo ~{target_duration_sec}s"

    draft = ScriptDraft.objects.create(
        niche=niche,
        trend_run=trend_run,
        llm_credential=llm_credential,
        topic=topic,
        target_duration_sec=target_duration_sec,
        title=str(data.get("title") or topic)[:200],
        body=str(data.get("body") or ""),
        hooks=str(data.get("hooks") or ""),
        cta=str(data.get("cta") or ""),
        hashtags=str(data.get("hashtags") or "")[:500],
        version=1,
        notes=research_note,
        ai_raw={
            "research": {
                "cutoff_days": EVIDENCE_MAX_AGE_DAYS,
                "published_after": evidence.get("published_after"),
                "videos": evidence.get("videos") or [],
                "articles": evidence.get("articles") or [],
                "errors": evidence.get("errors") or [],
                "sources_used": data.get("sources_used") or [],
                "claims_to_verify": data.get("claims_to_verify") or [],
                "target_duration_sec": target_duration_sec,
            }
        },
    )
    score_result = ai_detect.score_text(draft.body)
    # Preserva research no ai_raw junto com o score
    merged_raw = dict(draft.ai_raw or {})
    merged_raw["score"] = score_result.raw or {}
    draft.mark_scored(
        score_result.score,
        score_result.status,
        merged_raw,
    )
    return draft


def regenerate_script(
    draft: ScriptDraft,
    *,
    llm_credential: LlmCredential | None = None,
    target_duration_sec: int | None = None,
) -> ScriptDraft:
    """Cria nova versão com prompt anti-detecção + nova pesquisa."""
    cred = llm_credential if llm_credential is not None else draft.llm_credential
    duration = (
        _clamp_duration(target_duration_sec)
        if target_duration_sec is not None
        else _clamp_duration(draft.target_duration_sec or 60)
    )
    new = generate_script(
        draft.niche,
        draft.topic,
        trend_run=draft.trend_run,
        llm_credential=cred,
        anti_detect=True,
        target_duration_sec=duration,
    )
    new.version = draft.version + 1
    base_note = new.notes or ""
    new.notes = f"Regenerado a partir do draft #{draft.pk}. {base_note}".strip()
    new.save(update_fields=["version", "notes", "updated_at"])
    return new


def humanize_for_anti_ai(
    draft: ScriptDraft,
    *,
    llm_credential: LlmCredential | None = None,
) -> ScriptDraft:
    """
    Reescreve o texto para soar humano (burlar detector), sem mudar fatos/conteúdo.
    Em seguida recalcula o score anti-IA.
    """
    ensure_repo_on_path()
    cred = llm_credential if llm_credential is not None else draft.llm_credential
    new_body = _llm_humanize(draft.body, llm_credential=cred)
    if new_body.strip():
        draft.body = new_body.strip()
    draft.version = int(draft.version or 1) + 1
    note = (draft.notes or "").strip()
    tag = "Humanizado p/ anti-IA"
    draft.notes = f"{note} · {tag}".strip(" ·") if note else tag
    draft.save(update_fields=["body", "version", "notes", "updated_at"])
    return rescore(draft)


def create_duration_variant(
    draft: ScriptDraft,
    *,
    target_duration_sec: int,
    llm_credential: LlmCredential | None = None,
) -> ScriptDraft:
    """
    Cria um NOVO rascunho (versão curta/longa/etc.) a partir do roteiro atual.
    Mantém fatos e pesquisa; só recalibra o tamanho falado.
    """
    ensure_repo_on_path()
    target = _clamp_duration(target_duration_sec)
    cred = llm_credential if llm_credential is not None else draft.llm_credential
    data = _llm_resize_variant(
        draft,
        target_duration_sec=target,
        llm_credential=cred,
    )
    research = {}
    if isinstance(draft.ai_raw, dict):
        research = dict(draft.ai_raw.get("research") or {})
    research["target_duration_sec"] = target
    research["variant_of"] = draft.pk

    new = ScriptDraft.objects.create(
        niche=draft.niche,
        trend_run=draft.trend_run,
        llm_credential=cred,
        topic=draft.topic,
        target_duration_sec=target,
        title=str(data.get("title") or draft.title or draft.topic)[:200],
        body=str(data.get("body") or draft.body or ""),
        hooks=str(data.get("hooks") if data.get("hooks") is not None else draft.hooks),
        cta=str(data.get("cta") if data.get("cta") is not None else draft.cta),
        hashtags=str(data.get("hashtags") or draft.hashtags or "")[:500],
        version=1,
        notes=(
            f"Variante ~{target}s a partir do roteiro #{draft.pk} "
            f"(original ~{draft.target_duration_sec or '?'}s)"
        ),
        ai_raw={"research": research},
    )
    score_result = ai_detect.score_text(new.body)
    merged = dict(new.ai_raw or {})
    merged["score"] = score_result.raw or {}
    new.mark_scored(score_result.score, score_result.status, merged)
    return new


def rescore(draft: ScriptDraft) -> ScriptDraft:
    result = ai_detect.score_text(draft.body)
    merged = dict(draft.ai_raw or {})
    research = merged.get("research")
    merged["score"] = result.raw or {}
    if research is not None:
        merged["research"] = research
    draft.mark_scored(result.score, result.status, merged)
    return draft


def _clamp_duration(seconds: int | None) -> int:
    try:
        value = int(seconds or 60)
    except (TypeError, ValueError):
        value = 60
    return max(15, min(3600, value))


def _words_for_duration(seconds: int) -> tuple[int, int]:
    """Estimativa BR falado ~2.3–2.8 palavras/s."""
    lo = max(20, int(seconds * 2.2))
    hi = max(lo + 10, int(seconds * 2.9))
    return lo, hi


def _llm_humanize(
    body: str,
    *,
    llm_credential: LlmCredential | None = None,
) -> str:
    try:
        from app.services import llm

        prompt = f"""
Você é editor de roteiros falados no Brasil.
Reescreva o texto abaixo para PASSAR em detectores anti-IA (soar 100% humano),
SEM mudar o conteúdo: mesmos fatos, números, nomes, apostas e ordem das ideias.

Regras de estilo:
- Como criador BR falando no celular; contrações; ritmo irregular
- Frases curtas e longas misturadas; 1 imperfeição leve ok
- Proibido clichê de LLM ("Neste vídeo vamos…", "É importante ressaltar…", "Em conclusão…")
- NÃO use iniciais/siglas de pessoas ou marcas no lugar do nome (proibido RDJ, MJ, etc.)
- Use o nome como o público BR chama a pessoa/obra/aparelho (ex.: "Homem de Ferro", não só "Iron Man" se for o uso comum; "Robert Downey Jr.", nunca "RDJ")
- Não invente fatos novos; não remova fatos existentes

Texto original:
\"\"\"
{body}
\"\"\"

Responda SOMENTE com o roteiro reescrito (texto puro, sem JSON, sem markdown).
""".strip()
        with use_llm_credential(llm_credential):
            raw = llm._generate_response(prompt)
        text = (raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text).strip()
        return text or body
    except Exception:
        logger.exception("script humanize failed")
        return body


def _llm_resize_variant(
    draft: ScriptDraft,
    *,
    target_duration_sec: int,
    llm_credential: LlmCredential | None = None,
) -> dict:
    try:
        from app.services import llm

        words_lo, words_hi = _words_for_duration(target_duration_sec)
        src = draft.target_duration_sec or 60
        direction = (
            "ENCURTAR"
            if target_duration_sec < src
            else ("ALONGAR" if target_duration_sec > src else "RECALIBRAR")
        )
        prompt = f"""
Você adapta roteiros falados para o Brasil.
Tarefa: {direction} o roteiro abaixo para ~{target_duration_sec} segundos falados
(aprox. {words_lo}–{words_hi} palavras). Tolerância ±10%.

Regras:
- Mantenha os MESMOS fatos, números, apostas e easter eggs (não invente nem apague o essencial).
- Se ENCURTAR: corte enrolação e exemplos repetidos; preserve gancho + miolo + CTA.
- Se ALONGAR: aprofunde com contexto já implícito, ritmo falado, micro-detalhes — sem inventar estatística nova.
- Tom humano BR; sem clichê de LLM.
- Sem iniciais/siglas no lugar de nomes (proibido RDJ etc.); use o nome como o público BR chama.
- Idioma: {draft.niche.default_language if draft.niche_id else "pt-BR"}

Roteiro atual (~{src}s):
Título: {draft.title or draft.topic}
Tema: {draft.topic}
Body:
\"\"\"
{draft.body}
\"\"\"
Hooks: {draft.hooks or "n/a"}
CTA: {draft.cta or "n/a"}

Responda SOMENTE JSON:
{{
  "title": "título (pode ajustar levemente ao formato)",
  "body": "roteiro completo calibrado para ~{target_duration_sec}s",
  "hooks": "2-3 aberturas",
  "cta": "CTA",
  "hashtags": "{draft.hashtags or "#shorts"}"
}}
""".strip()
        with use_llm_credential(llm_credential):
            raw = llm._generate_response(prompt)
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0) if match else raw)
        if not isinstance(data, dict):
            raise ValueError("LLM não retornou objeto")
        return data
    except Exception as exc:
        logger.exception("script variant resize failed")
        return {
            "title": draft.title or draft.topic,
            "body": draft.body,
            "hooks": draft.hooks,
            "cta": draft.cta,
            "hashtags": draft.hashtags,
            "error": str(exc),
        }


def gather_script_evidence(
    niche: Niche,
    topic: str,
    *,
    trend_run: TrendRun | None = None,
) -> dict:
    """Vídeos quentes + artigos recentes (≤90 dias) sobre o tema."""
    published_after_dt = datetime.now(timezone.utc) - timedelta(days=EVIDENCE_MAX_AGE_DAYS)
    published_after = published_after_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    errors: list[str] = []
    videos: list[dict] = []
    seen_vids: set[str] = set()

    # Seeds do trend run (só se ainda recentes)
    if trend_run and trend_run.candidates_json:
        for c in trend_run.candidates_json[:12]:
            if not c.get("url"):
                continue
            pub = (c.get("published_at") or "")[:10]
            if pub and pub < published_after_dt.date().isoformat():
                continue
            vid = c.get("video_id") or c.get("url")
            if not vid or vid in seen_vids:
                continue
            seen_vids.add(str(vid))
            videos.append(_compact_video(c, query="trend_run"))

    yt_queries = _youtube_queries(niche, topic)
    for query, region, lang in yt_queries:
        try:
            from panel.channels import youtube as youtube_service

            batch = youtube_service.search_videos(
                query,
                max_results=5,
                order="viewCount",
                published_after=published_after,
                region_code=region,
                relevance_language=lang,
            )
            for item in batch:
                vid = item.get("video_id")
                if not vid or vid in seen_vids:
                    continue
                seen_vids.add(vid)
                videos.append(_compact_video(item, query=query))
        except Exception as exc:
            msg = f"youtube[{query}]: {exc}"
            errors.append(msg)
            logger.warning("script evidence YT failed: %s", exc)

    videos.sort(key=lambda v: int(v.get("view_count") or 0), reverse=True)
    videos = videos[:12]

    articles: list[dict] = []
    seen_arts: set[str] = set()
    for query, hl, gl, ceid in _news_queries(niche, topic):
        try:
            for art in _google_news_rss(query, hl=hl, gl=gl, ceid=ceid, limit=6):
                key = (art.get("url") or art.get("title") or "").lower()
                if not key or key in seen_arts:
                    continue
                seen_arts.add(key)
                articles.append(art)
        except Exception as exc:
            msg = f"news[{query}/{hl}]: {exc}"
            errors.append(msg)
            logger.warning("script evidence news failed: %s", exc)

    articles = articles[:14]

    return {
        "published_after": published_after,
        "cutoff_days": EVIDENCE_MAX_AGE_DAYS,
        "videos": videos,
        "articles": articles,
        "errors": errors,
    }


def _youtube_queries(niche: Niche, topic: str) -> list[tuple[str, str, str]]:
    """(query, region, relevance_language)."""
    topic = (topic or "").strip()
    niche_name = niche.name
    return [
        (topic, "BR", "pt"),
        (f"{topic} explicado", "BR", "pt"),
        (f"{topic} {niche_name}", "BR", "pt"),
        (f"{topic} explained", "US", "en"),
        (f"{topic} analysis", "US", "en"),
        (f"{topic} news", "US", "en"),
    ]


def _news_queries(niche: Niche, topic: str) -> list[tuple[str, str, str, str]]:
    """(query, hl, gl, ceid) para Google News RSS."""
    topic = (topic or "").strip()
    return [
        (f"{topic} when:90d", "pt-BR", "BR", "BR:pt-419"),
        (f"{topic} {niche.name} when:90d", "pt-BR", "BR", "BR:pt-419"),
        (f"{topic} when:90d", "en", "US", "US:en"),
        (f"{topic} magazine OR review when:90d", "en", "US", "US:en"),
    ]


def _compact_video(item: dict, *, query: str) -> dict:
    desc = unescape(str(item.get("description") or "")).strip()
    if len(desc) > 480:
        desc = desc[:477] + "…"
    return {
        "title": item.get("title") or "",
        "url": item.get("url") or "",
        "channel": item.get("channel_title") or item.get("channel") or "",
        "view_count": int(item.get("view_count") or 0),
        "like_count": int(item.get("like_count") or 0),
        "published_at": item.get("published_at") or "",
        "description": desc,
        "query": query or item.get("query") or "",
    }


def _google_news_rss(
    query: str,
    *,
    hl: str,
    gl: str,
    ceid: str,
    limit: int = 6,
) -> list[dict]:
    params = urlencode({"q": query, "hl": hl, "gl": gl, "ceid": ceid})
    url = f"https://news.google.com/rss/search?{params}"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=12) as resp:
        raw = resp.read()
    root = ET.fromstring(raw)
    items: list[dict] = []
    for node in root.findall("./channel/item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        pub = (node.findtext("pubDate") or "").strip()
        source_el = node.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        if not title:
            continue
        items.append(
            {
                "title": unescape(title),
                "url": link,
                "published_at": pub,
                "source": source,
                "lang": hl,
                "query": query,
            }
        )
        if len(items) >= limit:
            break
    return items


def _llm_script(
    niche: Niche,
    topic: str,
    *,
    anti_detect: bool,
    llm_credential: LlmCredential | None = None,
    evidence: dict | None = None,
    target_duration_sec: int = 60,
) -> dict:
    evidence = evidence or {}
    try:
        from app.services import llm

        anti = ""
        if anti_detect:
            anti = """
Modo anti-detecção: escreva como criador BR falando no celular.
Use contrações, frases curtas e longas misturadas, uma imperfeição leve,
evite listas simétricas e aberturas de LLM ("Neste vídeo vamos...", "É importante ressaltar").
"""
        target_duration_sec = _clamp_duration(target_duration_sec)
        words_lo, words_hi = _words_for_duration(target_duration_sec)
        videos_json = json.dumps(evidence.get("videos") or [], ensure_ascii=False)
        articles_json = json.dumps(evidence.get("articles") or [], ensure_ascii=False)
        cutoff = evidence.get("published_after") or f"últimos {EVIDENCE_MAX_AGE_DAYS} dias"

        prompt = f"""
Você escreve roteiros falados para shorts/reels no Brasil — densos, específicos, NÃO superficiais.

Nicho: {niche.name}
Briefing: {niche.briefing or "n/a"}
Tema pedido: {topic}
Idioma do roteiro: {niche.default_language or "pt-BR"}
DURAÇÃO ALVO FALADA: ~{target_duration_sec} segundos (±10% ok).
Volume aproximado: {words_lo}–{words_hi} palavras (ritmo de fala BR).
Não encha com enrolação nem corte o miolo: ajuste a densidade ao tempo.
Recorte temporal das fontes: a partir de {cutoff} (máx. {EVIDENCE_MAX_AGE_DAYS} dias). Ignore ou marque como velho qualquer dado claramente anterior.

NOMES E LOCALIZAÇÃO (obrigatório):
- NUNCA use iniciais/siglas no lugar de nomes (proibido RDJ, MJ, CR7 como único nome, etc.).
- Não faça "tradução automática" literal. Use o nome como o público do Brasil chama aquela pessoa, obra, marca ou aparelho.
  Exemplos: "Robert Downey Jr." (não RDJ); "Homem de Ferro" se for o uso comum no BR; "iPhone" permanece iPhone.
- Livros/filmes/séries: título pelo qual o público BR conhece.

VÍDEOS QUENTES (ordenados por views; use título+descrição para entender o ângulo — não invente o que não está aí):
{videos_json}

ARTIGOS / REVISTAS / NOTÍCIAS (PT-BR e outras línguas):
{articles_json}

Missão de pesquisa → roteiro:
1) Cruze os vídeos mais quentes com as matérias; priorize o que está bombando AGORA.
2) Sintetize um roteiro ORIGINAL (não copie um vídeo). Inclua:
   - gancho forte nos primeiros 3s
   - 2–4 fatos concretos (números, datas, nomes) vindos das fontes ou claramente rotulados como estimativa/aposta
   - pelo menos 1 "easter egg" / detalhe curioso que a maioria não cita
   - 1 "aposta" ou previsão do criador (deixar claro que é opinião/palpite)
   - 1 momento de validação: diga o que CONFERIU (ex.: "vi em X", "o vídeo Y com N views fala…") e o que NÃO dá para afirmar
3) Se as fontes forem fracas/vazias: diga no body que a pesquisa veio magra e foque no que há — NÃO invente estatísticas, odds ou quotes.
4) Tom conversacional BR, ritmo irregular, zero clichê de LLM.
{anti}

Responda SOMENTE JSON:
{{
  "title": "título curto clickável",
  "body": "roteiro completo para narração (falado), calibrado para ~{target_duration_sec}s",
  "hooks": "2-3 aberturas alternativas",
  "cta": "chamada para ação",
  "hashtags": "#tag1 #tag2",
  "sources_used": ["título ou url curta das fontes realmente usadas"],
  "claims_to_verify": ["afirmações fortes que o humano deve checar antes de gravar"]
}}
""".strip()
        with use_llm_credential(llm_credential):
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
            "sources_used": [],
            "claims_to_verify": [],
        }
