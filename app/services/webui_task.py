import threading
from collections import deque

from loguru import logger

from app.config import config
from app.controllers.manager.memory_manager import InMemoryTaskManager
from app.models import const
from app.models.schema import VideoParams
from app.services import state as sm
from app.services import task as tm
from app.utils.logging_utils import format_log_record


# A configuração do WebUI é armazenada em um dicionário global em nível de processo. A implementação de sincronização original será mantida durante a compilação completa
# runtime_config_lock, para que diferentes sessões do navegador sejam executadas em série. Aqui o número de simultaneidade é fixo
# É 1, que não apenas mantém a consistência da configuração original, mas também evita que vários threads esperem sem sentido fora do bloqueio de configuração.
_task_manager = InMemoryTaskManager(
    max_concurrent_tasks=1,
    max_queued_tasks=max(1, int(config.app.get("max_queued_tasks", 100))),
)
_task_logs: dict[str, deque[str]] = {}
_task_logs_lock = threading.RLock()
_MAX_LOG_TASKS = 20
_MAX_LOG_RECORDS_PER_TASK = 1000
# O Streamlit não pode enviar atualizações de componentes diretamente por threads em segundo plano, mas só pode ser pesquisado por meio do Fragment. 0,5 segundos
# É o suficiente para fazer o log da WebUI próximo à saída em tempo real do terminal, mas não continuará a ocupar recursos do navegador, como atualização de alta frequência.
TASK_LOG_REFRESH_INTERVAL_SECONDS = 0.5


def _append_task_log(task_id: str, message: str) -> None:
    """Salve um número limitado de logs por tarefa para pesquisa segura por Streamlit Fragments."""
    with _task_logs_lock:
        records = _task_logs.get(task_id)
        if records is None:
            # Mantenha apenas os logs das tarefas mais recentes para evitar que o serviço WebUI continue ocupando memória após um longo período de execução.
            # dict mantém o pedido de inserção; o log de tarefas é usado apenas para diagnóstico de interface e a eliminação do registro mais antigo não afeta a tarefa.
            if len(_task_logs) >= _MAX_LOG_TASKS:
                oldest_task_id = next(iter(_task_logs))
                _task_logs.pop(oldest_task_id, None)
            records = deque(maxlen=_MAX_LOG_RECORDS_PER_TASK)
            _task_logs[task_id] = records
        records.append(message.rstrip())


def get_task_logs(task_id: str) -> list[str]:
    """Retorne um instantâneo de log para evitar a retenção de bloqueios usados ​​por threads em segundo plano durante a renderização da página."""
    with _task_logs_lock:
        return list(_task_logs.get(task_id, ()))


def _run_generation(
    task_id: str,
    params: VideoParams,
    capture_logs: bool,
    voice_preview: dict | None = None,
) -> dict:
    """Execute o pipeline de vídeo existente em um thread em segundo plano.

    O coletor do Loguru é um recurso em nível de processo, portanto deve ser filtrado pelo thread de trabalho atual. Caso contrário, execute simultaneamente
    As tarefas da API ou outros logs de página são misturados com a tarefa atual. A página lê apenas instantâneos de lista comuns e não lê em segundo plano
    O thread acessa o Streamlit session_state para evitar a causa raiz da confusão do caminho delta durante a atualização."""
    log_handler_id = None
    worker_thread_id = threading.get_ident()
    try:
        if capture_logs:
            log_handler_id = logger.add(
                lambda message: _append_task_log(task_id, str(message)),
                level="DEBUG",
                format=format_log_record,
                colorize=False,
                filter=lambda record: record["thread"].id == worker_thread_id,
            )

        # A tarefa completa ainda usa o bloqueio de configuração original, evitando que outra sessão da WebUI a modifique no meio da construção
        # Configurações em nível de processo, como provedores e chaves, fazem com que configurações diferentes sejam usadas antes e depois do mesmo vídeo.
        with config.runtime_config_lock():
            return tm.start(
                task_id=task_id,
                params=params,
                voice_preview=voice_preview,
            )
    except Exception as exc:
        # tm.start já é responsável por converter exceções de pipeline em status de falha; aqui coletor de log de proteção adicional,
        # Camadas wrapper WebUI, como bloqueios de configuração. Qualquer exceção de thread em segundo plano deve deixar o estado final e não pode deixar a tarefa
        # O gerenciador continua mostrando "Construindo" permanentemente após a saída do thread de trabalho.
        error = f"{type(exc).__name__}: {exc}"
        failure = {
            "task_id": task_id,
            "state": const.TASK_STATE_FAILED,
            "progress": 0,
            "failed_stage": "webui_worker",
            "error": error,
        }
        sm.state.update_task(
            task_id,
            state=failure["state"],
            progress=failure["progress"],
            failed_stage=failure["failed_stage"],
            error=failure["error"],
        )
        logger.exception(
            f"unexpected WebUI generation worker failure, "
            f"task_id={task_id}, error={exc}"
        )
        return failure
    finally:
        if log_handler_id is not None:
            try:
                logger.remove(log_handler_id)
            except ValueError:
                logger.debug(
                    f"WebUI task log handler already removed: task_id={task_id}"
                )


def submit_generation(
    task_id: str,
    params: VideoParams,
    capture_logs: bool = True,
    voice_preview: dict | None = None,
) -> None:
    """Registre-se e envie a tarefa de geração de vídeo WebUI e retorne imediatamente após a chamada.

    O status da tarefa deve ser gravado antes do thread ser iniciado. Desta forma, a tarefa pode ser consultada quando a execução atual do script da página terminar.
    As atualizações do navegador ou as reconexões do WebSocket também não dependem de espaços reservados para páginas antigas na memória."""
    task_params = params.model_copy(deep=True)
    # A carga de visualização contém apenas caminhos de áudio imutáveis, instantâneos de parâmetros e uma linha do tempo de legendas somente leitura. Copie o dicionário externo,
    # Isso evita que novas execuções subsequentes da página afetem tarefas já enviadas para a fila em segundo plano ao substituir campos em cache.
    voice_preview_snapshot = dict(voice_preview) if voice_preview else None
    sm.state.update_task(
        task_id,
        state=const.TASK_STATE_PROCESSING,
        progress=0,
        video_subject=task_params.video_subject or task_params.video_script or task_id,
    )
    try:
        _task_manager.add_task(
            _run_generation,
            task_id=task_id,
            params=task_params,
            capture_logs=capture_logs,
            voice_preview=voice_preview_snapshot,
        )
    except Exception as exc:
        # Falhas de agendamento, como falhas de pipeline, devem se tornar consultáveis ​​para evitar exibição permanente no gerenciador de tarefas.
        # "Gerando". Preservar os tipos de exceção facilita a localização rápida de problemas de fila do Docker ou de logs nativos.
        error = f"{type(exc).__name__}: {exc}"
        sm.state.update_task(
            task_id,
            state=const.TASK_STATE_FAILED,
            progress=0,
            failed_stage="scheduling",
            error=error,
        )
        logger.exception(
            f"failed to submit WebUI generation task, task_id={task_id}, error={exc}"
        )
        raise
