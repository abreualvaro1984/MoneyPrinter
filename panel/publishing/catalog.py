from __future__ import annotations

"""
Plataformas relevantes para monetização por visualização / programas de criadores no Brasil.

Valores e requisitos mudam com frequência — trate como referência operacional, não garantia.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlatformField:
    key: str
    label: str
    required: bool = False
    help_text: str = ""
    max_length: int | None = None


@dataclass(frozen=True)
class PlatformSpec:
    id: str
    name: str
    pays_per_view_br: bool
    monetization_notes: str
    auth_mode: str  # oauth | token | upload_post | manual
    upload_api_ready: bool
    fields: tuple[PlatformField, ...] = field(default_factory=tuple)


# Campos comuns reutilizados
_TITLE = PlatformField("title", "Título", True, "Título principal do post/vídeo", 100)
_DESCRIPTION = PlatformField(
    "description", "Descrição / caption", True, "Texto longo + CTA + hashtags quando couber"
)
_TAGS = PlatformField("tags", "Tags", False, "Lista separada por vírgula (YouTube) ou hashtags")
_HASHTAGS = PlatformField(
    "hashtags", "Hashtags", False, "Ex.: #financas #shorts (sem ou com #)"
)
_PRIVACY = PlatformField(
    "privacy", "Privacidade", True, "public / unlisted / private (ou equivalente)"
)
_THUMBNAIL = PlatformField(
    "thumbnail_path", "Thumbnail customizada", False, "Caminho de imagem JPG/PNG"
)
_SCHEDULE = PlatformField(
    "scheduled_at", "Agendar publicação", False, "ISO datetime (se a API permitir)"
)
_LANGUAGE = PlatformField("language", "Idioma", False, "pt-BR")
_MADE_FOR_KIDS = PlatformField(
    "made_for_kids", "Feito para crianças", False, "true/false (obrigatório no YouTube)"
)
_CATEGORY = PlatformField("category_id", "Categoria", False, "YouTube categoryId, ex.: 22")
_COVER_TIME = PlatformField(
    "cover_time_ms", "Frame de capa (ms)", False, "Usado em TikTok/Reels quando suportado"
)
_DISABLE_COMMENT = PlatformField("disable_comment", "Desativar comentários", False)
_BRAND_CONTENT = PlatformField(
    "brand_content", "Conteúdo de marca / paid partnership", False
)


PLATFORM_SPECS: dict[str, PlatformSpec] = {
    "youtube": PlatformSpec(
        id="youtube",
        name="YouTube (longos + Shorts)",
        pays_per_view_br=True,
        monetization_notes=(
            "YPP no Brasil: ~1.000 inscritos + 4.000h/12 meses OU 10M views Shorts/90 dias. "
            "Longos pagam bem mais por view que Shorts. Upload via YouTube Data API oficial."
        ),
        auth_mode="oauth",
        upload_api_ready=True,
        fields=(
            PlatformField("title", "Título", True, "Máx. 100 caracteres", 100),
            PlatformField("description", "Descrição", True, "Máx. ~5000; use #shorts em Shorts"),
            PlatformField("tags", "Tags", False, "Até ~500 caracteres no total"),
            _PRIVACY,
            _CATEGORY,
            _MADE_FOR_KIDS,
            _THUMBNAIL,
            _LANGUAGE,
            _SCHEDULE,
            PlatformField(
                "contains_synthetic_media",
                "Mídia sintética / IA",
                False,
                "Marcar quando o vídeo for gerado/alterado por IA",
            ),
        ),
    ),
    "tiktok": PlatformSpec(
        id="tiktok",
        name="TikTok",
        pays_per_view_br=True,
        monetization_notes=(
            "Creator Rewards / Creativity Program no BR: tipicamente 10k seguidores + "
            "100k views/30 dias; vídeos longos (>1 min) pagam melhor. "
            "Upload: TikTok Content Posting API (OAuth) ou gateway Upload-Post."
        ),
        auth_mode="oauth",
        upload_api_ready=True,
        fields=(
            PlatformField("title", "Caption / título", True, "Texto do post (limite ~2200)", 2200),
            _HASHTAGS,
            PlatformField(
                "privacy",
                "Privacidade",
                True,
                "PUBLIC_TO_EVERYONE / MUTUAL_FOLLOW_FRIENDS / SELF_ONLY",
            ),
            _COVER_TIME,
            _DISABLE_COMMENT,
            _BRAND_CONTENT,
            PlatformField("duet_disabled", "Desativar dueto", False),
            PlatformField("stitch_disabled", "Desativar stitch", False),
            _SCHEDULE,
        ),
    ),
    "instagram": PlatformSpec(
        id="instagram",
        name="Instagram Reels",
        pays_per_view_br=True,
        monetization_notes=(
            "Pagamento por view é irregular (bônus/convite). Forte em brand deals. "
            "Upload: Instagram Graph API (conta Professional + Facebook Page) ou Upload-Post."
        ),
        auth_mode="oauth",
        upload_api_ready=True,
        fields=(
            PlatformField("caption", "Caption", True, "Texto do Reel + hashtags", 2200),
            _HASHTAGS,
            _THUMBNAIL,
            _COVER_TIME,
            PlatformField("share_to_feed", "Compartilhar no feed", False, "true/false"),
            _BRAND_CONTENT,
            _SCHEDULE,
        ),
    ),
    "facebook": PlatformSpec(
        id="facebook",
        name="Facebook / Reels",
        pays_per_view_br=True,
        monetization_notes=(
            "In-stream ads / Reels play (quando elegível). Upload via Graph API (Page token)."
        ),
        auth_mode="token",
        upload_api_ready=True,
        fields=(
            PlatformField("description", "Descrição", True, "Texto do post"),
            _TITLE,
            _PRIVACY,
            _THUMBNAIL,
            _SCHEDULE,
        ),
    ),
    "kwai": PlatformSpec(
        id="kwai",
        name="Kwai",
        pays_per_view_br=True,
        monetization_notes=(
            "Programa de criadores no BR com barreira menor que TikTok; RPM variável. "
            "API pública de upload para criadores é limitada — conector inicia em modo manual/gateway."
        ),
        auth_mode="manual",
        upload_api_ready=False,
        fields=(
            PlatformField("caption", "Legenda", True, "Texto do post"),
            _HASHTAGS,
            _PRIVACY,
        ),
    ),
}


def list_platforms() -> list[PlatformSpec]:
    return list(PLATFORM_SPECS.values())


def get_platform(platform_id: str) -> PlatformSpec:
    try:
        return PLATFORM_SPECS[platform_id]
    except KeyError as exc:
        raise KeyError(f"Plataforma desconhecida: {platform_id}") from exc


def required_field_keys(platform_id: str) -> list[str]:
    return [f.key for f in get_platform(platform_id).fields if f.required]
