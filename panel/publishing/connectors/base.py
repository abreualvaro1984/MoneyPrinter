from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from panel.publishing.catalog import required_field_keys
from panel.publishing.models import SocialAccount


@dataclass
class PublishResult:
    success: bool
    remote_id: str = ""
    remote_url: str = ""
    raw: dict = field(default_factory=dict)
    error: str = ""


class Connector(Protocol):
    platform: str

    def validate_account(self, account: SocialAccount) -> tuple[bool, str]:
        ...

    def upload(
        self, account: SocialAccount, video_path: str, metadata: dict
    ) -> PublishResult:
        ...


def validate_metadata(platform: str, metadata: dict) -> list[str]:
    """Return list of missing required logical fields."""
    missing = []
    for key in required_field_keys(platform):
        # caption aliases description/title for IG/TikTok
        value = metadata.get(key)
        if key == "caption" and not value:
            value = metadata.get("description") or metadata.get("title")
        if key == "title" and not value:
            value = metadata.get("caption")
        if key == "description" and not value:
            value = metadata.get("caption")
        if value is None or value == "":
            missing.append(key)
    return missing
