# Publisher

## Missão

Gerenciar contas sociais por nicho e publicar vídeos com metadados corretos por plataforma.

## Já existe

- `panel/publishing/models.py` — `SocialAccount`, `PublishTarget`
- Conectores: YouTube OAuth, Upload-Post (TikTok/IG/FB), Kwai manual
- Catálogo de campos: `panel/publishing/catalog.py`, `PLATFORMS.md`

## Regras

- Várias contas por plataforma
- Metadados: title, description/caption, tags, hashtags, privacy, etc.
- Job com destinos usa `publish_job_targets`; senão fallback canal YouTube legado

## UI futura

Área **Contas** / **Publicar** (além do Admin).

## Status

Backend parcial; UI amigável no roadmap item 6.
