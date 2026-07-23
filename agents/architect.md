# Architect — MoneyPrinter

## Stack

| Camada | Tecnologia |
|--------|------------|
| Motor de vídeo | `app/` (MoneyPrinterTurbo: LLM, TTS, material, task) |
| Painel | Django em `panel/` |
| UI amigável | `panel/ui` — templates + HTMX + CSS (tema escuro colorido) |
| Admin | Django Admin (fallback operacional) |
| Workers | `panel/jobs/worker.py` + `manage.py process_jobs` |
| Publicação | `panel/publishing/` (conectores YouTube / Upload-Post / Kwai) |

## Pastas importantes

- `panel/niches/` — nichos
- `panel/research/` — research snapshots (legado + base YT)
- `panel/jobs/` — create / clip / dub / research jobs
- `panel/publishing/` — SocialAccount, PublishTarget
- `panel/ui/` — UI HTMX (shell + Trends + Roteiros)
- `agents/` — contexto de agentes
- `roadmap.md` — progresso

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
