# Architect — MoneyPrinter

## Stack

| Camada | Tecnologia |
|--------|------------|
| Motor de vídeo | `app/` (MoneyPrinterTurbo: LLM, TTS, material, task) |
| Painel | Django em `panel/` |
| UI amigável | `panel/ui` — templates + HTMX + CSS (tema escuro colorido, mobile-first) |
| Admin | Django Admin (fallback operacional) |
| Workers | `panel/jobs/worker.py` + `manage.py process_jobs` |
| Publicação | `panel/publishing/` (conectores YouTube / Upload-Post / Kwai) |

## Pastas importantes

- `panel/niches/` — nichos
- `panel/channels/` — YouTube OAuth + Data API client (`youtube.py`)
- `panel/research/` — research snapshots (legado + base YT)
- `panel/jobs/` — create / clip / dub / research jobs
- `panel/publishing/` — SocialAccount, PublishTarget
- `panel/ui/` — UI HTMX (shell + Trends + Roteiros + Nichos + APIs)
- `agents/` — contexto de agentes
- `.cursor/skills/moneyprinter-panel/` — skill Cursor do painel
- `roadmap.md` — progresso

## Credenciais (UI `/apis/`)

| Tipo | Onde | Modelo / resolve |
|------|------|------------------|
| YouTube Data API | `/apis/` (banco) + Testar | `YoutubeDataApiKey`; fallback `YOUTUBE_API_KEY` |
| LLMs | `/apis/` + Testar | `LlmCredential` |
| Anti-IA Gemini | env ou Gemini em `/apis/` | `ai_detect.py` |
| OAuth upload | JSON + canal | separado da API key de pesquisa |

## Convenções

- Python 3.14+, `uv` no WSL preferencialmente.
- Respostas ao usuário em português.
- Workspace: `F:\Projetos\MoneyPrinter`.
- Não commitar `config.toml`, `panel/.env`, tokens.
- Mudanças mínimas e focadas; não misturar refactors com features.
- Ao concluir feature do roadmap: atualizar checkboxes.

## UI

- Dark theme com acentos coloridos (CSS variables).
- SEO: `lang="pt-BR"`, title/meta por view.
- Auth: login Django session (mesmo user do admin).
- Overlay “IA pensando” em forms com classe `js-ai-wait`.
