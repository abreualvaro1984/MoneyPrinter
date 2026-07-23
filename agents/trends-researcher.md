# Trends Researcher

## Missão

Descobrir o que está **quente em views** para um nicho e devolver temas com recomendação **add** ou **skip**. Não gera vídeo.

## Entradas

- Nicho (obrigatório)
- Plataformas
- Credencial de IA (`LlmCredential`) escolhida na UI — várias keys permitidas

## Saídas

- Resumo PT
- Topics com: `title`, `why`, `recommendation` (`add`|`skip`), `heat_score` (0–100), `view_count` quando houver, `ref_url`

## Fontes

1. **YouTube** — search `order=viewCount` + `videos.list` statistics; ordenar por views reais.
2. **Outras** — heuristic sem inventar métricas.

## Agentes: vale a pena?

- **Sim para contexto Cursor** — este arquivo + `agents/` (já existe).
- **Runtime multi-agent (researcher → critic → ranker):** útil depois, quando volume crescer. Hoje um único prompt estruturado com views reais + add/skip é o melhor custo/benefício.
- Não criar orquestração multi-agente só por moda — só se a qualidade do add/skip degradar.

## Código

- `panel/ui/services/trends.py`
- `panel/ui/services/llm_runtime.py`
- UI: `/trends/`, `/apis/`
