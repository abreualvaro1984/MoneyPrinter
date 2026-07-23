from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from panel.jobs.engine_path import ensure_repo_on_path
from panel.ui.models import LlmCredential


@contextmanager
def use_llm_credential(credential: LlmCredential | None) -> Iterator[None]:
    """Sobrescreve temporariamente config.app para usar a credencial do painel."""
    if credential is None:
        yield
        return

    ensure_repo_on_path()
    from app.config import config, runtime_config_lock

    provider = (credential.provider or "").strip()
    if not provider:
        yield
        return

    overrides = {
        "llm_provider": provider,
        f"{provider}_api_key": credential.api_key,
    }
    if credential.model_name.strip():
        overrides[f"{provider}_model_name"] = credential.model_name.strip()
    if credential.base_url.strip():
        overrides[f"{provider}_base_url"] = credential.base_url.strip()

    previous: dict = {}
    with runtime_config_lock():
        for key, value in overrides.items():
            previous[key] = config.app.get(key)
            config.app[key] = value
        try:
            yield
        finally:
            for key, old in previous.items():
                if old is None:
                    config.app.pop(key, None)
                else:
                    config.app[key] = old


def resolve_credential(pk: int | None) -> LlmCredential | None:
    if pk:
        cred = LlmCredential.objects.filter(pk=pk, is_active=True).first()
        if cred:
            return cred
    return (
        LlmCredential.objects.filter(is_active=True, is_default=True).first()
        or LlmCredential.objects.filter(is_active=True).first()
    )
