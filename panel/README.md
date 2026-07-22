# Painel MoneyPrinter — Fábrica pessoal multi-nicho (YouTube)

Operação diária via **Django Admin** + worker local. O Streamlit legado continua disponível, mas não é necessário.

## O que tem

| Módulo | Função |
|--------|--------|
| **Nichos** | Briefing, keywords, voz PT-BR, aspect, fonte de material |
| **Canais** | 1 conta YouTube OAuth por nicho |
| **Jobs** | `create` / `clip` / `dub` / `research` + revisão + upload |
| **Pesquisa** | Busca YouTube por keywords do nicho + sugestões LLM |

## Setup (WSL recomendado)

```bash
cd /mnt/f/Projetos/MoneyPrinter
export PATH="$HOME/.local/bin:$PATH"
uv sync --extra panel
cp panel/.env.example panel/.env

# Motor de vídeo ainda usa config.toml na raiz (LLM + Pexels etc.)
# test -f config.toml || cp config.example.toml config.toml

mkdir -p panel/credentials
# Coloque o OAuth client JSON em:
#   panel/credentials/youtube_client_secret.json
# E a YOUTUBE_API_KEY no panel/.env

cd panel
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver 127.0.0.1:8000
```

Admin: http://127.0.0.1:8000/admin/

## Fluxo típico

1. Crie um **Nicho** (keywords + briefing + voz `pt-BR-FranciscaNeural-Female`).
2. Crie o **Canal YouTube** ligado ao nicho → botão **Conectar OAuth**.
3. Nos Nichos, ação **Pesquisar trends/YouTube** (ou Job tipo `research`).
4. No snapshot, **Gerar jobs Create/Clip**.
5. Abra o Job → **Enfileirar** (ou rode o worker).
6. Quando status = *Aguardando revisão* → **Aprovar + Upload**.

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
   `http://127.0.0.1:8000/channels/oauth/callback/`
3. Baixe o JSON → `panel/credentials/youtube_client_secret.json`
4. Crie também uma **API Key** para pesquisa → `YOUTUBE_API_KEY` no `.env`

## Observações

- Create reusa `app.services.task` (stock + TTS).
- Clip: `yt-dlp` + Whisper + LLM para timestamps + FFmpeg.
- Dub: Whisper → tradução LLM → TTS PT → troca de áudio (sem lip-sync).
- Upload oficial via YouTube Data API (privacidade default: `private`).
- Artefatos: `storage/niches/<slug>/<job-uuid>/`
