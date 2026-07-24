# Trends Researcher

## Missão

Descobrir o que está **quente em views** para um nicho e devolver temas com recomendação **add** ou **skip**. Não gera vídeo.

## Entradas

- Nicho (obrigatório)
- Plataformas
- Credencial de IA (`LlmCredential`) escolhida na UI — várias keys permitidas
- YouTube Data API key em `/apis/` (banco; prioridade sobre `.env`; botão **Testar**)
- Formato de vídeo (dark / dormir / tela preta / ambiente / aparecendo / …)

## Saídas

- Resumo PT
- Topics com: `title`, `why`, `recommendation` (`add`|`skip`), `heat_score` (0–100), `view_count` quando houver, `ref_url`

## Fontes

1. **YouTube** — search `order=viewCount` + `videos.list` statistics; ordenar por views reais.
2. **Descoberta de nichos** — `videos.list(chart=mostPopular, regionCode=BR)` + buscas recentes por views; a IA só agrupa evidências (`panel/ui/services/niches_discover.py`).
3. **Formato de vídeo** — usuário escolhe em `/nichos/`: dark, sleep (dormir), blackscreen (tela preta), ambient, face, hybrid, screen, any. Seeds + prompt + `format_fit`/`format_ok` em `video_formats.py`.
4. **Outras** — heuristic sem inventar métricas.

## Credenciais YouTube

- UI `/apis/` → modelo `YoutubeDataApiKey` (singleton).
- Resolve: `panel/channels/youtube.py` → `resolve_youtube_api_key()` (db → env).
- Validação: `panel/ui/services/youtube_test.py` (mostPopular BR, 1 item).
- Não aceitar `GOCSPX-` (client secret OAuth).

## Código

- `panel/ui/services/trends.py`
- `panel/ui/services/niches_discover.py`
- `panel/ui/services/llm_runtime.py`
- `panel/channels/youtube.py`
- UI: `/trends/`, `/nichos/` (histórico de pesquisas + Add in-place), `/apis/`
