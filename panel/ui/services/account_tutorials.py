from __future__ import annotations

"""Tutoriais de cadastro de contas sociais: o que preencher e onde pegar."""

ACCOUNT_TUTORIALS: dict[str, dict] = {
    "youtube": {
        "title": "YouTube — como conectar",
        "steps": [
            "No Google Cloud Console, crie um projeto e ative a YouTube Data API v3.",
            "Crie um OAuth Client (Web) com redirect: http://127.0.0.1:8010/channels/oauth/callback/",
            "Baixe o JSON do client e salve em panel/credentials/youtube_client_secret.json.",
            "Neste painel, crie a conta com plataforma YouTube e modo OAuth.",
            "Depois use o Admin → Canais YouTube → Conectar OAuth (fluxo legado) ou cole o token JSON em Credenciais.",
            "Preencha: Nome amigável, Username (@canal), ID externo (channelId opcional), privacidade padrão.",
        ],
        "fields": {
            "name": "Nome interno, ex.: YT Finanças Principal",
            "username": "Handle do canal, ex.: @meucanal",
            "external_id": "channelId (UC…), opcional",
            "auth_mode": "Use OAuth",
            "credentials_json": "JSON com token, refresh_token, client_id, client_secret (após OAuth)",
            "default_privacy": "private | unlisted | public",
        },
        "credentials_example": (
            '{\n  "token": "...",\n  "refresh_token": "...",\n'
            '  "token_uri": "https://oauth2.googleapis.com/token",\n'
            '  "client_id": "...",\n  "client_secret": "..."\n}'
        ),
        "links": [
            ("Google Cloud Console", "https://console.cloud.google.com/"),
            ("YouTube Data API", "https://developers.google.com/youtube/v3"),
        ],
    },
    "tiktok": {
        "title": "TikTok — via Upload-Post (recomendado)",
        "steps": [
            "Crie conta em upload-post.com e gere uma API key.",
            "Conecte sua conta TikTok no painel do Upload-Post.",
            "Aqui no MoneyPrinter: plataforma TikTok, modo Upload-Post.",
            "Em Credenciais cole: {\"api_key\": \"...\", \"username\": \"seu-user-upload-post\"}.",
            "Preencha Nome e Username (@tiktok).",
        ],
        "fields": {
            "name": "Ex.: TikTok Curiosidades 1",
            "username": "@handle do TikTok",
            "auth_mode": "upload_post",
            "credentials_json": "api_key + username do Upload-Post",
            "default_privacy": "PUBLIC_TO_EVERYONE (ou equivalente)",
        },
        "credentials_example": '{\n  "api_key": "sua-chave",\n  "username": "usuario-upload-post"\n}',
        "links": [
            ("Upload-Post", "https://upload-post.com/"),
            ("TikTok for Developers", "https://developers.tiktok.com/"),
        ],
    },
    "instagram": {
        "title": "Instagram Reels — via Upload-Post",
        "steps": [
            "Conta Instagram Professional ligada a uma Página do Facebook.",
            "No Upload-Post, conecte o Instagram.",
            "Cadastre aqui com modo Upload-Post e o JSON de api_key + username.",
            "Preencha Nome e Username (@ig).",
        ],
        "fields": {
            "name": "Ex.: IG Reels Beleza",
            "username": "@instagram",
            "auth_mode": "upload_post",
            "credentials_json": "api_key + username Upload-Post",
        },
        "credentials_example": '{\n  "api_key": "sua-chave",\n  "username": "usuario-upload-post"\n}',
        "links": [
            ("Upload-Post", "https://upload-post.com/"),
            ("Meta for Developers", "https://developers.facebook.com/"),
        ],
    },
    "facebook": {
        "title": "Facebook / Reels — Upload-Post ou Page token",
        "steps": [
            "Opção A (simples): Upload-Post com api_key + username.",
            "Opção B: Graph API — Page access token + page_id no JSON.",
            "Cadastre Nome, Username da página e credenciais.",
        ],
        "fields": {
            "name": "Ex.: FB Página Finanças",
            "username": "Nome da Página",
            "external_id": "page_id (se usar token direto)",
            "auth_mode": "upload_post ou token",
            "credentials_json": "Upload-Post OU {\"access_token\":\"...\",\"page_id\":\"...\"}",
        },
        "credentials_example": '{\n  "api_key": "sua-chave",\n  "username": "usuario-upload-post"\n}',
        "links": [
            ("Upload-Post", "https://upload-post.com/"),
            ("Meta Graph API", "https://developers.facebook.com/docs/graph-api"),
        ],
    },
    "kwai": {
        "title": "Kwai — cadastro manual",
        "steps": [
            "A API pública de upload ainda é limitada neste painel.",
            "Cadastre Nome + Username/@ para organizar por nicho.",
            "Modo Manual: o sistema valida metadados; você publica o arquivo no app Kwai.",
        ],
        "fields": {
            "name": "Ex.: Kwai Humor BR",
            "username": "@kwai ou ID",
            "auth_mode": "manual",
            "credentials_json": "pode ficar vazio no modo manual",
        },
        "credentials_example": "{}",
        "links": [
            ("Kwai Criadores", "https://www.kwai.com/"),
        ],
    },
}


def tutorial_for(platform: str) -> dict:
    return ACCOUNT_TUTORIALS.get(platform) or {
        "title": "Tutorial",
        "steps": ["Selecione a plataforma para ver o passo a passo."],
        "fields": {},
        "credentials_example": "{}",
        "links": [],
    }
