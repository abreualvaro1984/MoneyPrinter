# Painel MoneyPrinter — Fábrica pessoal multi-nicho

Operação diária via **UI Django+HTMX** (`/`) + **Admin** + worker local.

Roadmap e agentes: [`../roadmap.md`](../roadmap.md) · [`../agents/`](../agents/)

## O que tem

| Módulo | Função |
|--------|--------|
| **UI** (`panel/ui`) | Tema escuro mobile-first: Trends + Roteiros + Nichos + APIs + Contas |
| **Nichos** | Briefing, keywords, descoberta com sinais YouTube reais |
| **APIs** (`/apis/`) | YouTube Data key (banco + Testar) e keys de IA (Testar) |
| **Canais** | Canal YouTube OAuth (upload; separado da API key de pesquisa) |
| **Contas sociais** | Várias contas YouTube / TikTok / IG / Facebook / Kwai |
| **Destinos de publicação** | Metadados + upload por conta (inline no Job) |
| **Jobs** | `create` / `clip` / `dub` / `research` + revisão + publish |
| **Pesquisa** | Busca YouTube por keywords (base dos Trends) |

Detalhes de plataformas: [`publishing/PLATFORMS.md`](publishing/PLATFORMS.md).

## Setup (WSL recomendado)

```bash
cd /mnt/f/Projetos/MoneyPrinter
export PATH="$HOME/.local/bin:$PATH"
uv sync --extra panel
cp panel/.env.example panel/.env

# Motor de vídeo ainda usa config.toml na raiz (LLM + Pexels etc.)
# test -f config.toml || cp config.example.toml config.toml

mkdir -p panel/credentials
# OAuth (upload): panel/credentials/youtube_client_secret.json
# API key de pesquisa: preferência = digitar em http://127.0.0.1:8010/apis/
# (YOUTUBE_API_KEY no .env é só fallback)

cd panel
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver 127.0.0.1:8010
```

Admin: http://127.0.0.1:8010/admin/  
UI: http://127.0.0.1:8010/ · Cadastro: http://127.0.0.1:8010/cadastro/

Usuário bootstrap (se rodou `bootstrap_panel`): **admin** / **admin**  
Novas contas: tela de cadastro; senhas com **bcrypt**.

## Fluxo típico

1. Crie um **Nicho** (keywords + briefing + voz `pt-BR-FranciscaNeural-Female`).
2. Cadastre **Contas sociais** (várias por plataforma) e/ou o Canal YouTube legado OAuth.
3. Nos Nichos, ação **Pesquisar trends/YouTube** (ou Job tipo `research`).
4. No snapshot, **Gerar jobs Create/Clip**.
5. Abra o Job → **Enfileirar** (ou rode o worker).
6. No Job, na seção **Destinos de publicação**, vincule contas e preencha título/descrição/tags.
7. Quando status = *Aguardando revisão* → **Aprovar + Upload** (usa destinos; senão cai no canal YouTube legado).

### Worker em loop

```bash
cd panel
uv run python manage.py process_jobs --loop --interval 10
```

### CLI rápida

```bash
uv run python manage.py create_job meu-nicho --type create --subject "Tema do short" --enqueue
uv run python manage.py create_job meu-nicho --type clip --source-url "https://youtube.com/watch?v=..." --cut-topic "momento chave" --enqueue
uv run python manage.py create_job meu-nicho --type dub --source-url "https://youtube.com/watch?v=..." --subject "Podcast EN→PT" --enqueue
```

## Google Cloud

1. Crie projeto no Google Cloud → ative **YouTube Data API v3**.
2. Crie OAuth Client **Web application** com redirect  
   `http://127.0.0.1:8010/channels/oauth/callback/`
3. Baixe o JSON → `panel/credentials/youtube_client_secret.json`
4. Crie também uma **API Key** para pesquisa → cole em **UI `/apis/`** (banco).
   Opcional: `YOUTUBE_API_KEY` no `.env` como fallback. Use o botão **Testar**.

## Observações

- Create reusa `app.services.task` (stock + TTS).
- Clip: `yt-dlp` + Whisper + LLM para timestamps + FFmpeg.
- Dub: Whisper → tradução LLM → TTS PT → troca de áudio (sem lip-sync).
- Upload oficial via YouTube Data API (privacidade default: `private`).
- Artefatos: `storage/niches/<slug>/<job-uuid>/`
