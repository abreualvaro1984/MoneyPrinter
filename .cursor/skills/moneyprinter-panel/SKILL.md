---
name: moneyprinter-panel
description: >-
  Contexto do painel MoneyPrinter (Django+HTMX): Trends, Nichos, Roteiros anti-IA,
  APIs (YouTube + LLMs). Use ao implementar ou depurar features do painel em
  F:\Projetos\MoneyPrinter, ou quando o usuário falar de /apis/, nichos, trends,
  roteiros, Gemini anti-IA ou YouTube Data API.
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
| APIs | `/apis/` | YouTube Data key + keys de IA | Upload OAuth |
| Contas | `/contas/` | Social accounts por nicho | Pesquisa |

## Credenciais

- **YouTube Data API (pesquisa/nichos/trends):** digitar em `/apis/` → salva em `YoutubeDataApiKey` (banco). Botões Salvar + **Testar**. Ordem de resolve: banco → `YOUTUBE_API_KEY` no `.env`. Nunca `GOCSPX-` (OAuth secret).
- **IAs (ChatGPT, Gemini, Kimi, DeepSeek, Z.AI):** `/apis/` → `LlmCredential`. Botão Testar por linha.
- **Anti-IA:** Gemini (Google AI Studio) via `GEMINI_API_KEY` ou credencial Gemini em `/apis/`; fallback GPTZero / heurística. Código: `panel/ui/services/ai_detect.py`.
- **OAuth upload:** `youtube_client_secret.json` + canal conectado — separado da API key de pesquisa.

## Código-chave

- YouTube client / resolve key: `panel/channels/youtube.py` (`resolve_youtube_api_key`)
- Teste YouTube: `panel/ui/services/youtube_test.py`
- Descoberta nichos: `panel/ui/services/niches_discover.py` + filtro `video_formats.py` (dark/face/…)
- Trends: `panel/ui/services/trends.py`
- Roteiros + score: `panel/ui/services/scripts.py`, `ai_detect.py`

## Rodar (WSL + uv)

```bash
cd /mnt/f/Projetos/MoneyPrinter/panel
uv run python manage.py migrate
uv run python manage.py runserver 127.0.0.1:8010
```

Não commitar `config.toml`, `panel/.env`, tokens. Ao concluir item do roadmap: marcar `[x]`.
