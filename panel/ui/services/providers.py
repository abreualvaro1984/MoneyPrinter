from __future__ import annotations

"""Presets de IA do painel: só a API key é pedida ao usuário."""

from dataclasses import dataclass

from panel.jobs.engine_path import ensure_repo_on_path


@dataclass(frozen=True)
class PanelLlmPreset:
    provider_id: str
    label: str
    key_url: str
    howto: str


# ChatGPT, Gemini, Kimi, DeepSeek e Z.AI — URLs/modelos vêm do registry do motor.
PANEL_LLM_PRESETS: tuple[PanelLlmPreset, ...] = (
    PanelLlmPreset(
        "openai",
        "ChatGPT (OpenAI)",
        "https://platform.openai.com/api-keys",
        "1) Acesse platform.openai.com → API keys → Create. 2) Cole a key aqui. Modelo e URL são preenchidos automaticamente.",
    ),
    PanelLlmPreset(
        "gemini",
        "Gemini (Google)",
        "https://aistudio.google.com/app/apikey",
        "1) Abra Google AI Studio → Get API key. 2) Cole a key. Modelo default do Gemini é aplicado sozinho.",
    ),
    PanelLlmPreset(
        "moonshot",
        "Kimi (Moonshot)",
        "https://platform.kimi.ai/console/api-keys",
        "1) Abra platform.kimi.ai (plataforma global em inglês) → API Keys → Create. 2) Cole a key. Base URL global (api.moonshot.ai) e modelo são automáticos. Não use platform.kimi.com (China/chinês) — as keys não são intercambiáveis.",
    ),
    PanelLlmPreset(
        "deepseek",
        "DeepSeek",
        "https://platform.deepseek.com/api_keys",
        "1) platform.deepseek.com → API Keys. 2) Cole a key. URL e modelo DeepSeek são automáticos.",
    ),
    PanelLlmPreset(
        "zai",
        "Z.AI",
        "https://z.ai/manage-apikey/apikey-list",
        "1) Faça login em z.ai → Manage API Key (lista de keys). 2) Crie/copie a key e cole aqui. Endpoint api.z.ai e modelo GLM são aplicados automaticamente. Docs: docs.z.ai/guides/overview/quick-start",
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


def provider_choices() -> list[tuple[str, str]]:
    """Compat: lista completa do motor (admin legado). Preferir panel_provider_choices na UI."""
    ensure_repo_on_path()
    from app.models.llm_provider import LLM_PROVIDER_REGISTRY

    return [(p.provider_id, p.default_label) for p in LLM_PROVIDER_REGISTRY]
