from __future__ import annotations

"""Presets de IA do painel: API key + modelo selecionável."""

import json
from dataclasses import dataclass

from panel.jobs.engine_path import ensure_repo_on_path


@dataclass(frozen=True)
class PanelLlmPreset:
    provider_id: str
    label: str
    key_url: str
    howto: str
    """Modelos sugeridos na UI (primeiro = default preferido na lista)."""
    models: tuple[str, ...] = ()


# ChatGPT, Gemini, Grok, Kimi, DeepSeek e Z.AI — URLs/modelos vêm do registry do motor.
PANEL_LLM_PRESETS: tuple[PanelLlmPreset, ...] = (
    PanelLlmPreset(
        "openai",
        "ChatGPT (OpenAI)",
        "https://platform.openai.com/api-keys",
        "1) Acesse platform.openai.com → API keys → Create. 2) Cole a key aqui. 3) Escolha o modelo.",
        models=("gpt-5.5", "gpt-5.4-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini"),
    ),
    PanelLlmPreset(
        "gemini",
        "Gemini (Google)",
        "https://aistudio.google.com/app/apikey",
        "1) Abra Google AI Studio → Get API key. 2) Cole a key. 3) Escolha o modelo (Flash é mais barato).",
        models=(
            "gemini-3.1-pro-preview",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ),
    ),
    PanelLlmPreset(
        "grok",
        "Grok (xAI)",
        "https://console.x.ai/",
        "1) Abra console.x.ai → API Keys → Create. 2) Cole a key. 3) Escolha o modelo Grok.",
        models=(
            "grok-4.5",
            "grok-4.3",
            "grok-4",
            "grok-4-fast-reasoning",
            "grok-4-fast-non-reasoning",
            "grok-3",
            "grok-3-mini",
        ),
    ),
    PanelLlmPreset(
        "moonshot",
        "Kimi (Moonshot)",
        "https://platform.kimi.ai/console/api-keys",
        "1) Abra platform.kimi.ai → API Keys → Create. 2) Cole a key. 3) Escolha o modelo. Não use platform.kimi.com (China).",
        models=("kimi-k2.7-code", "kimi-k2.5", "moonshot-v1-auto", "moonshot-v1-128k", "moonshot-v1-32k"),
    ),
    PanelLlmPreset(
        "deepseek",
        "DeepSeek",
        "https://platform.deepseek.com/api_keys",
        "1) platform.deepseek.com → API Keys. 2) Cole a key. 3) Escolha o modelo.",
        models=("deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner", "deepseek-coder"),
    ),
    PanelLlmPreset(
        "zai",
        "Z.AI",
        "https://z.ai/manage-apikey/apikey-list",
        "1) z.ai → Manage API Key. 2) Cole a key. 3) Escolha o modelo GLM.",
        models=("glm-5.2", "glm-4.7", "glm-4.6", "glm-4.5-flash", "glm-4-flash"),
    ),
)


def panel_provider_choices() -> list[tuple[str, str]]:
    return [(p.provider_id, p.label) for p in PANEL_LLM_PRESETS]


def get_panel_preset(provider_id: str) -> PanelLlmPreset | None:
    for p in PANEL_LLM_PRESETS:
        if p.provider_id == provider_id:
            return p
    return None


def apply_provider_defaults(provider_id: str) -> dict:
    """Retorna model_name e base_url padrão do registry do motor."""
    ensure_repo_on_path()
    from app.models.llm_provider import get_llm_provider

    spec = get_llm_provider(provider_id)
    if not spec:
        return {"model_name": "", "base_url": ""}
    return {
        "model_name": spec.default_model or "",
        "base_url": spec.default_base_url or "",
    }


def panel_models_for(provider_id: str) -> list[str]:
    """Lista de modelos sugeridos: preset + default do registry do motor."""
    models: list[str] = []
    preset = get_panel_preset(provider_id)
    if preset and preset.models:
        models.extend(preset.models)
    defaults = apply_provider_defaults(provider_id)
    default_model = (defaults.get("model_name") or "").strip()
    if default_model and default_model not in models:
        models.insert(0, default_model)
    return list(dict.fromkeys(m for m in models if m))


def panel_model_choices(provider_id: str, *, extra: str = "") -> list[tuple[str, str]]:
    models = panel_models_for(provider_id)
    extra = (extra or "").strip()
    if extra and extra not in models:
        models = [extra, *models]
    if not models:
        return [("", "— default do sistema —")]
    return [(m, m) for m in models]


def provider_choices() -> list[tuple[str, str]]:
    """Compat: lista completa do motor (admin legado). Preferir panel_provider_choices na UI."""
    ensure_repo_on_path()
    from app.models.llm_provider import LLM_PROVIDER_REGISTRY

    return [(p.provider_id, p.default_label) for p in LLM_PROVIDER_REGISTRY]


def models_catalog_json() -> str:
    """JSON {provider_id: [models...]} para o JS do formulário."""
    catalog = {p.provider_id: panel_models_for(p.provider_id) for p in PANEL_LLM_PRESETS}
    return json.dumps(catalog, ensure_ascii=False)
