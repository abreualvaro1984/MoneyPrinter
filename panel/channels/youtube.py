from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .models import YouTubeChannel


def _client_secrets_path() -> Path:
    path = Path(settings.YOUTUBE_CLIENT_SECRETS)
    if not path.exists():
        raise FileNotFoundError(
            f"Arquivo de client secret não encontrado: {path}. "
            "Baixe o OAuth Desktop/Web client no Google Cloud Console."
        )
    return path


def build_authorization_url(channel: YouTubeChannel) -> str:
    flow = Flow.from_client_secrets_file(
        str(_client_secrets_path()),
        scopes=settings.YOUTUBE_SCOPES,
        redirect_uri=settings.YOUTUBE_OAUTH_REDIRECT_URI,
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    # Keep state → channel mapping in DB field last_error temporarily is bad;
    # use a sidecar file under credentials.
    state_file = Path(settings.BASE_DIR) / "credentials" / "oauth_states.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    states = {}
    if state_file.exists():
        states = json.loads(state_file.read_text(encoding="utf-8"))
    states[state] = channel.pk
    state_file.write_text(json.dumps(states), encoding="utf-8")
    return auth_url


def finish_authorization(code: str, state: str) -> YouTubeChannel:
    state_file = Path(settings.BASE_DIR) / "credentials" / "oauth_states.json"
    states = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    channel_id = states.pop(state, None)
    state_file.write_text(json.dumps(states), encoding="utf-8")
    if not channel_id:
        raise ValueError("Estado OAuth inválido ou expirado.")

    channel = YouTubeChannel.objects.get(pk=channel_id)
    flow = Flow.from_client_secrets_file(
        str(_client_secrets_path()),
        scopes=settings.YOUTUBE_SCOPES,
        redirect_uri=settings.YOUTUBE_OAUTH_REDIRECT_URI,
        state=state,
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or settings.YOUTUBE_SCOPES),
    }
    channel.set_token_data(token_data)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    mine = youtube.channels().list(part="snippet", mine=True).execute()
    items = mine.get("items") or []
    if items:
        channel.channel_id = items[0]["id"]
        channel.title = items[0]["snippet"]["title"]
    channel.status = YouTubeChannel.Status.CONNECTED
    channel.connected_at = timezone.now()
    channel.last_error = ""
    channel.save()
    Path(channel.credentials_path()).write_text(
        json.dumps(token_data, indent=2), encoding="utf-8"
    )
    return channel


def get_credentials(channel: YouTubeChannel) -> Credentials:
    data = channel.get_token_data()
    if not data:
        raise ValueError(f"Canal {channel} sem token OAuth.")
    creds = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes") or settings.YOUTUBE_SCOPES,
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        data["token"] = creds.token
        channel.set_token_data(data)
        channel.save(update_fields=["token_json", "updated_at"])
    return creds


def upload_video(
    channel: YouTubeChannel,
    file_path: str,
    *,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    privacy_status: str = "private",
    category_id: str = "22",
    made_for_kids: bool = False,
) -> dict:
    creds = get_credentials(channel)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": (tags or [])[:30],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": made_for_kids,
        },
    }
    media = MediaFileUpload(file_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response


def search_videos(query: str, *, max_results: int = 10, order: str = "viewCount") -> list[dict]:
    """Search public YouTube videos. Uses API key if set, else first connected channel."""
    api_key = (settings.YOUTUBE_API_KEY or "").strip()
    if api_key:
        youtube = build("youtube", "v3", developerKey=api_key, cache_discovery=False)
    else:
        channel = YouTubeChannel.objects.filter(status=YouTubeChannel.Status.CONNECTED).first()
        if not channel:
            raise RuntimeError(
                "Configure YOUTUBE_API_KEY no panel/.env ou conecte ao menos um canal OAuth."
            )
        youtube = build(
            "youtube", "v3", credentials=get_credentials(channel), cache_discovery=False
        )

    search = (
        youtube.search()
        .list(
            q=query,
            part="snippet",
            type="video",
            maxResults=max_results,
            order=order,
            relevanceLanguage="pt",
            regionCode="BR",
        )
        .execute()
    )
    results = []
    for item in search.get("items") or []:
        video_id = item["id"]["videoId"]
        snippet = item["snippet"]
        results.append(
            {
                "video_id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": snippet.get("title", ""),
                "channel_title": snippet.get("channelTitle", ""),
                "description": snippet.get("description", ""),
                "published_at": snippet.get("publishedAt", ""),
                "thumbnail": (snippet.get("thumbnails") or {}).get("medium", {}).get("url", ""),
            }
        )
    return results
