---
name: moneyprinter-panel
description: >-
  Contexto do painel MoneyPrinter (Django+HTMX): Trends, Nichos, Roteiros anti-IA,
  Plano de vídeo, APIs (YouTube + LLMs). Use ao implementar ou depurar features do
  painel em F:\Projetos\MoneyPrinter, ou quando o usuário falar de /apis/, nichos,
  trends, roteiros, planos, Gemini anti-IA ou YouTube Data API.
---

# MoneyPrinter — painel

Workspace: `F:\Projetos\MoneyPrinter`. Responder em **português**.  
Antes de codar: ler `roadmap.md` + agent em `agents/` da área tocada.

## Áreas (não misturar numa ação)

| Área | Rota | Faz | Não faz |
|------|------|-----|---------|
| Nichos | `/nichos/` | Descoberta ancorada em sinais YT + Add | Gerar vídeo |
| Trends | `/trends/` | Temas quentes + Usar este tema | Render |
| Roteiros | `/roteiros/` | Texto humano + score anti-IA | Enfileirar create sozinho |
| Plano | `/planos/` | Roteiro+assets+voz+dub sugeridos | Render / enfileirar Create-Dub |
| APIs | `/apis/` | YouTube Data key + keys de IA | Upload OAuth |
| Contas | `/contas/` | Social accounts por nicho | Pesquisa |

## Credenciais

- **YouTube Data API (pesquisa/nichos/trends/planos):** digitar em `/apis/` → salva em `YoutubeDataApiKey` (banco). Botões Salvar + **Testar**. Ordem: banco → `YOUTUBE_API_KEY` no `.env`. Nunca `GOCSPX-`.
- **IAs:** `/apis/` → `LlmCredential` (ChatGPT, Gemini, Grok, Kimi, DeepSeek, Z.AI). Botão Testar por linha.
- **Anti-IA:** Gemini via `GEMINI_API_KEY` ou credencial Gemini; fallback GPTZero / heurística (`ai_detect.py`).
- **OAuth upload:** JSON + canal — separado da API key de pesquisa.

## Código-chave

- YouTube: `panel/channels/youtube.py` (`resolve_youtube_api_key`)
- Plano: `panel/ui/services/video_plans.py` + modelo `VideoPlan`
- Nichos: `niches_discover.py` + `video_formats.py`
- Trends / Roteiros: `trends.py`, `scripts.py`, `ai_detect.py`

## Rodar (WSL + uv)

```bash
cd /mnt/f/Projetos/MoneyPrinter/panel
uv run python manage.py migrate
uv run python manage.py runserver 127.0.0.1:8010
```

Não commitar `config.toml`, `panel/.env`, tokens. Ao concluir item do roadmap: marcar `[x]`.
