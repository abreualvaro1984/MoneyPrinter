# Plataformas que monetizam no Brasil (referência operacional)

## Quem paga por visualização / programa de criadores?

| Plataforma | Paga no BR? | Como | Upload neste painel |
|---|---|---|---|
| **YouTube** | Sim (melhor RPM em longos) | YPP / Shorts funnel | OAuth oficial (`youtube`) |
| **TikTok** | Sim | Creator Rewards / Creativity | Upload-Post ou OAuth futuro |
| **Instagram Reels** | Parcial / variável | Bônus, brand deals | Upload-Post / Graph API |
| **Facebook** | Sim (quando elegível) | In-stream / Reels | Upload-Post / Page token |
| **Kwai** | Sim | Programa de criadores | Manual (API limitada) |

> Números de RPM e thresholds mudam. Cadastre várias contas e diversifique.

## Cadastro de contas

Admin → **Contas sociais**

Você pode cadastrar **várias contas** por plataforma (ex.: 3 YouTube + 2 TikTok).

Campos principais:
- Nome amigável
- Plataforma
- Nicho (opcional)
- Username / ID externo
- Modo de auth: OAuth | Token | Upload-Post | Manual
- `credentials_json`
- Privacidade padrão

### Exemplos de `credentials_json`

**YouTube OAuth**
```json
{
  "token": "...",
  "refresh_token": "...",
  "token_uri": "https://oauth2.googleapis.com/token",
  "client_id": "...",
  "client_secret": "...",
  "scopes": ["https://www.googleapis.com/auth/youtube.upload"]
}
```

**Upload-Post (TikTok / Instagram / Facebook)**
```json
{
  "api_key": "sua-chave",
  "username": "usuario-upload-post"
}
```

## Metadados para subir o vídeo (Destinos de publicação)

Para cada Job, crie um ou mais **Destinos de publicação** (PublishTarget), um por conta.

### YouTube
| Campo | Obrigatório | Notas |
|---|---|---|
| title | sim | ≤ 100 |
| description | sim | use `#shorts` em vertical |
| tags | não | vírgulas |
| privacy | sim | private/unlisted/public |
| category_id | não | default 22 |
| made_for_kids | não | exigência de conformidade |
| thumbnail_path | não | |
| contains_synthetic_media | não | recomendado para IA |

### TikTok
| Campo | Obrigatório | Notas |
|---|---|---|
| title (caption) | sim | ≤ ~2200 |
| hashtags | não | |
| privacy | sim | PUBLIC_TO_EVERYONE etc. |
| cover_time_ms | não | |
| disable_comment / duet / stitch | não | |
| brand_content | não | |

### Instagram Reels
| Campo | Obrigatório | Notas |
|---|---|---|
| caption | sim | description/title também aceitos |
| hashtags | não | |
| thumbnail_path / cover_time_ms | não | |
| share_to_feed | não | |
| brand_content | não | |

### Facebook
| Campo | Obrigatório | Notas |
|---|---|---|
| description | sim | |
| title | recomendado | |
| privacy | sim | |

### Kwai
| Campo | Obrigatório | Notas |
|---|---|---|
| caption | sim | publicação manual no app por enquanto |

## Fluxo sugerido

1. Cadastre contas em **Contas sociais**
2. Gere o vídeo (Job Create/Clip/Dub)
3. Em **Destinos de publicação**, vincule o Job às contas e preencha título/descrição/tags
4. Ação admin **Publicar agora nos conectores**
