from __future__ import annotations

from panel.jobs.engine_path import ensure_repo_on_path


def provider_choices() -> list[tuple[str, str]]:
    ensure_repo_on_path()
    from app.models.llm_provider import LLM_PROVIDER_REGISTRY

    return [(p.provider_id, p.default_label) for p in LLM_PROVIDER_REGISTRY]
