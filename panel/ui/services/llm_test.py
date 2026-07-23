from __future__ import annotations

from dataclasses import dataclass

from panel.jobs.engine_path import ensure_repo_on_path
from panel.ui.models import LlmCredential
from panel.ui.services.llm_runtime import use_llm_credential
from panel.ui.services.providers import apply_provider_defaults


@dataclass(frozen=True)
class LlmTestResult:
    ok: bool
    message: str
    elapsed: float
    reply_preview: str = ""


def test_llm_credential(credential: LlmCredential) -> LlmTestResult:
    """Envia um prompt mínimo com a credencial e reporta sucesso/falha."""
    ensure_repo_on_path()
    from app.services import llm as llm_service

    try:
        with use_llm_credential(credential):
            ok, err, elapsed = llm_service.test_connection()
    except Exception as exc:  # noqa: BLE001 — reportar qualquer falha ao usuário
        return LlmTestResult(
            ok=False,
            message=f"{type(exc).__name__}: {exc}",
            elapsed=0.0,
        )

    if ok:
        return LlmTestResult(
            ok=True,
            message=f"API OK — respondeu em {elapsed:.1f}s",
            elapsed=elapsed,
        )
    return LlmTestResult(
        ok=False,
        message=err or "Falha ao chamar a API",
        elapsed=elapsed,
    )


def test_llm_draft(provider: str, api_key: str) -> LlmTestResult:
    """Testa provider + key do formulário (ainda sem salvar)."""
    provider = (provider or "").strip()
    api_key = (api_key or "").strip()
    if not provider:
        return LlmTestResult(False, "Selecione a IA antes de testar.", 0.0)
    if not api_key:
        return LlmTestResult(False, "Cole a API key antes de testar.", 0.0)

    defaults = apply_provider_defaults(provider)
    draft = LlmCredential(
        name="__test__",
        provider=provider,
        api_key=api_key,
        model_name=defaults.get("model_name") or "",
        base_url=defaults.get("base_url") or "",
    )
    return test_llm_credential(draft)
