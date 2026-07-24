"""Verifique se existe uma nova versão oficial do MoneyPrinterTurbo disponível."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

import requests
from loguru import logger
from packaging.version import InvalidVersion, Version


LATEST_RELEASE_API_URL: Final = (
    "https://api.github.com/repos/harry0703/MoneyPrinterTurbo/releases/latest"
)
LATEST_RELEASE_PAGE_URL: Final = (
    "https://github.com/harry0703/MoneyPrinterTurbo/releases/latest"
)
# A verificação de atualizações é apenas uma função auxiliar e as anomalias da rede não podem retardar significativamente a WebUI local. Restrições separadas em conexões e leituras
# O período de tempo limite não apenas permite que o GitHub conclua a resposta na rede normal, mas também evita longas esperas em ambiente offline.
RELEASE_CHECK_TIMEOUT: Final = (1.0, 2.0)
RELEASE_CHECK_HEADERS: Final = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "MoneyPrinterTurbo-Version-Checker",
}
UPDATE_CHECK_CACHE_TTL_SECONDS: Final = 12 * 60 * 60


def _parse_version(value: str) -> Version:
    """Compatível com as tags ``v1.2.3`` comumente usadas do GitHub e convertidas para versões comparáveis."""
    normalized = str(value or "").strip()
    if normalized.lower().startswith("v"):
        normalized = normalized[1:]
    return Version(normalized)


def get_available_update(current_version: str) -> str | None:
    """Retorna a versão oficial mais recente superior à versão atual; retorna None se não houver atualizações ou se a verificação falhar.

    A interface ``releases/latest`` do GitHub exclui automaticamente versões de rascunho e pré-lançamento, então não há mais
    Implemente a filtragem de status de lançamento repetidamente. WebUI chama esta função em segundo plano através de ``AsyncUpdateChecker``;
    Quando a rede, o formato de resposta ou o rótulo da versão estiverem anormais, apenas os logs serão registrados e rebaixados para "Não exibir notificações", o que não afetará
    Funções principais, como geração de vídeo."""
    try:
        installed_version = _parse_version(current_version)
    except InvalidVersion:
        logger.warning(
            f"skip update check because current version is invalid: {current_version!r}"
        )
        return None

    try:
        response = requests.get(
            LATEST_RELEASE_API_URL,
            headers=RELEASE_CHECK_HEADERS,
            timeout=RELEASE_CHECK_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        # As falhas na verificação de atualização são exceções não essenciais recuperáveis. Retenha tipos de exceção e informações para facilitar a localização de agentes,
        # DNS, limitação do GitHub ou problemas de corrupção de resposta, evitando perturbar os usuários regulares na WebUI.
        logger.debug(
            "GitHub release check failed: "
            f"error_type={type(exc).__name__}, error={exc}"
        )
        return None

    if not isinstance(payload, dict):
        logger.debug(
            "GitHub release check returned an invalid payload: "
            f"payload_type={type(payload).__name__}"
        )
        return None

    tag_name = payload.get("tag_name", "")
    try:
        latest_version = _parse_version(tag_name)
    except InvalidVersion:
        logger.warning(
            f"skip update notification because release tag is invalid: {tag_name!r}"
        )
        return None

    if latest_version <= installed_version:
        return None

    normalized_latest_version = str(latest_version)
    logger.info(
        "MoneyPrinterTurbo update available: "
        f"current={installed_version}, latest={normalized_latest_version}"
    )
    return normalized_latest_version


@dataclass(frozen=True)
class UpdateCheckSnapshot:
    """O status imediato da versão em segundo plano verifica a leitura sem bloqueio pela WebUI."""

    complete: bool
    available_version: str | None = None


class AsyncUpdateChecker:
    """Execute a verificação de versão em um thread em segundo plano e armazene em cache os resultados mais recentes.

    Streamlit executará o script da página desde o início após qualquer interação de controle. Se acessado diretamente na área do título
    GitHub bloqueia a página inteira quando aberta pela primeira vez ou o cache expira. Aqui a solicitação de rede é colocada no thread daemon,
    A página lê apenas o instantâneo atual; após a conclusão da verificação, o resultado é atualizado uma vez pelo fragmento de curto prazo da WebUI.

    O resultado, seja "Atualização encontrada" ou "Sem atualização/falha de rede", será armazenado em cache para evitar o GitHub
    Quando estiver inacessível, será solicitado novamente sempre que for executado novamente. O bloqueio protege apenas o estado da memória e não envolve a solicitação de rede, portanto
    Não impede que outras sessões leiam o status da verificação."""

    def __init__(
        self,
        check: Callable[[str], str | None] = get_available_update,
        ttl_seconds: float = UPDATE_CHECK_CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._check = check
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._current_version: str | None = None
        self._available_version: str | None = None
        self._completed_at: float | None = None
        self._checking = False

    def poll(self, current_version: str) -> UpdateCheckSnapshot:
        """Volte para verificar o instantâneo imediatamente; inicie uma nova verificação em segundo plano quando o cache expirar."""
        normalized_current_version = str(current_version or "").strip()
        now = self._clock()

        with self._lock:
            cache_is_fresh = (
                self._current_version == normalized_current_version
                and self._completed_at is not None
                and now - self._completed_at < self._ttl_seconds
            )
            if cache_is_fresh:
                return UpdateCheckSnapshot(
                    complete=True,
                    available_version=self._available_version,
                )

            if (
                self._checking
                and self._current_version == normalized_current_version
            ):
                return UpdateCheckSnapshot(complete=False)

            # Quando a versão for alterada ou o cache expirar, os resultados antigos não deverão continuar a ser exibidos. Limpe o status primeiro e depois comece
            # Um novo thread para que o chamador obtenha um instantâneo explícito do pendente durante a verificação.
            self._current_version = normalized_current_version
            self._available_version = None
            self._completed_at = None
            self._checking = True

            worker = threading.Thread(
                target=self._run_check,
                args=(normalized_current_version,),
                name="mpt-version-check",
                daemon=True,
            )
            worker.start()

        return UpdateCheckSnapshot(complete=False)

    def _run_check(self, current_version: str) -> None:
        try:
            available_version = self._check(current_version)
        except Exception:
            # get_available_update lida com exceções esperadas de rede e dados. Este é o tópico de fundo
            # Finalmente, para proteger o limite, a pilha completa deve ser registrada para evitar pendências permanentes após exceção inesperada e encerramento silencioso.
            logger.exception(
                "unexpected error while checking for a MoneyPrinterTurbo update"
            )
            available_version = None

        with self._lock:
            # Em casos raros, a versão pode mudar durante a operação. Threads antigos não devem substituir novas versões de estado.
            if self._current_version != current_version:
                return
            self._available_version = available_version
            self._completed_at = self._clock()
            self._checking = False


_ASYNC_UPDATE_CHECKER = AsyncUpdateChecker()


def poll_available_update(current_version: str) -> UpdateCheckSnapshot:
    """Leia o status do verificador de antecedentes global para evitar solicitações repetidas ao GitHub para diferentes sessões do Streamlit."""
    return _ASYNC_UPDATE_CHECKER.poll(current_version)
